

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
import sqlite3
from typing import Generator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from .database import DATABASE_PATH, connect, initialize_database
from .money import rupees, settle, split_equally, to_paise


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Expense Sharing Service", version="1.0.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


def get_db() -> Generator[sqlite3.Connection, None, None]:
    db = connect()
    try:
        yield db
    finally:
        db.close()


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class MemberIn(BaseModel):
    user_id: int = Field(gt=0)


class ExpenseSplitIn(BaseModel):
    user_id: int = Field(gt=0)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class ExpenseIn(BaseModel):
    paid_by: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    description: str = Field(min_length=1, max_length=240)
    splits: list[ExpenseSplitIn] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("description cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_unique_split_users(self):
        if self.splits is not None:
            user_ids = [split.user_id for split in self.splits]
            if len(user_ids) != len(set(user_ids)):
                raise ValueError("each user can appear only once in splits")
        return self


class UserOut(BaseModel):
    id: int
    name: str


class GroupOut(BaseModel):
    id: int
    name: str


class ExpenseShareOut(BaseModel):
    user_id: int
    name: str
    amount: Decimal


class ExpenseOut(BaseModel):
    id: int
    group_id: int
    description: str
    amount: Decimal
    paid_by: UserOut
    split_type: str
    splits: list[ExpenseShareOut]
    created_at: str


def require_group(db: sqlite3.Connection, group_id: int) -> sqlite3.Row:
    group = db.execute("SELECT id, name FROM groups WHERE id = ?", (group_id,)).fetchone()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@app.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: NameIn, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("INSERT INTO users(name) VALUES (?)", (payload.name,))
    db.commit()
    return {"id": cursor.lastrowid, "name": payload.name}


@app.get("/users", response_model=list[UserOut])
def list_users(db: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in db.execute("SELECT id, name FROM users ORDER BY id")]


@app.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(payload: NameIn, db: sqlite3.Connection = Depends(get_db)):
    cursor = db.execute("INSERT INTO groups(name) VALUES (?)", (payload.name,))
    db.commit()
    return {"id": cursor.lastrowid, "name": payload.name}


@app.get("/groups", response_model=list[GroupOut])
def list_groups(db: sqlite3.Connection = Depends(get_db)):
    return [dict(row) for row in db.execute("SELECT id, name FROM groups ORDER BY id DESC")]


@app.delete("/groups/{group_id}")
def delete_group(group_id: int, db: sqlite3.Connection = Depends(get_db)):
    group = require_group(db, group_id)
   
    try:
        with db:
            db.execute(
                "DELETE FROM expense_shares WHERE expense_id IN "
                "(SELECT id FROM expenses WHERE group_id=?)", (group_id,)
            )
            db.execute("DELETE FROM expenses WHERE group_id=?", (group_id,))
            db.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
            db.execute("DELETE FROM groups WHERE id=?", (group_id,))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="This group still has linked records that prevented deletion. Restart the app and try again.",
        ) from exc
    return {"deleted": True, "group_id": group_id, "name": group["name"]}


@app.post("/groups/{group_id}/members", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def add_member(group_id: int, payload: MemberIn, db: sqlite3.Connection = Depends(get_db)):
    require_group(db, group_id)
    user = db.execute("SELECT id, name FROM users WHERE id = ?", (payload.user_id,)).fetchone()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        
        with db:
            db.execute("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", (group_id, payload.user_id))
            members = [row[0] for row in db.execute(
                "SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id", (group_id,)
            )]
            expenses = db.execute(
                "SELECT id, amount_paise FROM expenses WHERE group_id=? AND split_type='equal' ORDER BY id",
                (group_id,),
            ).fetchall()
            for expense_row in expenses:
                shares = split_equally(expense_row["amount_paise"], members)
                db.execute("DELETE FROM expense_shares WHERE expense_id=?", (expense_row["id"],))
                db.executemany(
                    "INSERT INTO expense_shares(expense_id, user_id, share_paise) VALUES (?, ?, ?)",
                    [(expense_row["id"], user_id, share) for user_id, share in shares.items()],
                )
    except sqlite3.IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="User is already a member of this group")
    return dict(user)


@app.get("/groups/{group_id}/members", response_model=list[UserOut])
def list_members(group_id: int, db: sqlite3.Connection = Depends(get_db)):
    require_group(db, group_id)
    return [dict(row) for row in db.execute(
        "SELECT u.id, u.name FROM users u JOIN group_members gm ON gm.user_id=u.id "
        "WHERE gm.group_id=? ORDER BY u.id", (group_id,)
    )]


@app.delete("/groups/{group_id}/members/{user_id}")
def remove_member(group_id: int, user_id: int, db: sqlite3.Connection = Depends(get_db)):
    require_group(db, group_id)
    user = db.execute(
        "SELECT u.id, u.name FROM users u JOIN group_members gm ON gm.user_id=u.id "
        "WHERE gm.group_id=? AND u.id=?", (group_id, user_id)
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=404, detail="Member not found in this group")

    member_ids = [row[0] for row in db.execute(
        "SELECT user_id FROM group_members WHERE group_id=? AND user_id<>? ORDER BY user_id",
        (group_id, user_id),
    )]
    expenses = db.execute(
        "SELECT id, amount_paise FROM expenses WHERE group_id=? AND split_type='equal' ORDER BY id",
        (group_id,),
    ).fetchall()
    try:
        with db:
            db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (group_id, user_id))
            if member_ids:
                for expense in expenses:
                    shares = split_equally(expense["amount_paise"], member_ids)
                    db.execute("DELETE FROM expense_shares WHERE expense_id=?", (expense["id"],))
                    db.executemany(
                        "INSERT INTO expense_shares(expense_id, user_id, share_paise) VALUES (?, ?, ?)",
                        [(expense["id"], user_id, share) for user_id, share in shares.items()],
                    )
            else:
              
                db.execute(
                    "DELETE FROM expense_shares WHERE expense_id IN "
                    "(SELECT id FROM expenses WHERE group_id=? AND split_type='equal')", (group_id,)
                )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Could not remove this member from the group") from exc

    return {"removed": True, "group_id": group_id, "member": dict(user)}


@app.post("/groups/{group_id}/expenses", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(group_id: int, payload: ExpenseIn, db: sqlite3.Connection = Depends(get_db)):
    require_group(db, group_id)
    members = [row[0] for row in db.execute(
        "SELECT user_id FROM group_members WHERE group_id=? ORDER BY user_id", (group_id,)
    )]
    if not members:
        raise HTTPException(status_code=400, detail="Add at least one group member before recording an expense")
    if payload.paid_by not in members:
        raise HTTPException(status_code=400, detail="Payer must be a member of the group")
    try:
        amount_paise = to_paise(payload.amount)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    split_type = "equal"
    if payload.splits is None:
        shares = split_equally(amount_paise, members)
    else:
        split_type = "custom"
        member_set = set(members)
        if any(split.user_id not in member_set for split in payload.splits):
            raise HTTPException(status_code=400, detail="Every split participant must be a member of the group")
        try:
            shares = {split.user_id: to_paise(split.amount) for split in payload.splits}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if sum(shares.values()) != amount_paise:
            raise HTTPException(status_code=422, detail="Custom split amounts must add up to the expense total")
    try:
        with db:
            cursor = db.execute(
                "INSERT INTO expenses(group_id, paid_by, amount_paise, description, split_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (group_id, payload.paid_by, amount_paise, payload.description, split_type),
            )
            expense_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO expense_shares(expense_id, user_id, share_paise) VALUES (?, ?, ?)",
                [(expense_id, user_id, share) for user_id, share in shares.items()],
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail="Could not record expense") from exc
    row = db.execute(
        "SELECT e.id, e.group_id, e.description, e.amount_paise, e.split_type, e.created_at, "
        "u.id AS user_id, u.name "
        "FROM expenses e JOIN users u ON u.id=e.paid_by WHERE e.id=?", (expense_id,)
    ).fetchone()
    return {"id": row["id"], "group_id": row["group_id"], "description": row["description"],
            "amount": rupees(row["amount_paise"]), "paid_by": {"id": row["user_id"], "name": row["name"]},
            "split_type": row["split_type"], "splits": _expense_split_details(expense_id, db),
            "created_at": row["created_at"]}


def _expense_split_details(expense_id: int, db: sqlite3.Connection):
    rows = db.execute(
        "SELECT s.user_id, u.name, s.share_paise FROM expense_shares s "
        "JOIN users u ON u.id=s.user_id WHERE s.expense_id=? ORDER BY s.user_id", (expense_id,)
    )
    return [{"user_id": row["user_id"], "name": row["name"], "amount": rupees(row["share_paise"])}
            for row in rows]


def _list_expenses(group_id: int, db: sqlite3.Connection):
    require_group(db, group_id)
    rows = db.execute(
        "SELECT e.id, e.group_id, e.description, e.amount_paise, e.split_type, e.created_at, "
        "u.id AS user_id, u.name "
        "FROM expenses e JOIN users u ON u.id=e.paid_by WHERE e.group_id=? ORDER BY e.created_at, e.id",
        (group_id,),
    )
    return [{"id": r["id"], "group_id": r["group_id"], "description": r["description"],
             "amount": rupees(r["amount_paise"]), "paid_by": {"id": r["user_id"], "name": r["name"]},
             "split_type": r["split_type"], "splits": _expense_split_details(r["id"], db),
             "created_at": r["created_at"]} for r in rows]


@app.get("/groups/{group_id}/expenses", response_model=list[ExpenseOut])
def list_expenses(group_id: int, db: sqlite3.Connection = Depends(get_db)):
    return _list_expenses(group_id, db)


def _balances(group_id: int, db: sqlite3.Connection):
    require_group(db, group_id)
    members = db.execute(
        "SELECT DISTINCT u.id, u.name FROM users u WHERE "
        "u.id IN (SELECT user_id FROM group_members WHERE group_id=?) "
        "OR u.id IN (SELECT e.paid_by FROM expenses e WHERE e.group_id=? "
        "AND EXISTS (SELECT 1 FROM expense_shares s WHERE s.expense_id=e.id)) "
        "OR u.id IN (SELECT s.user_id FROM expense_shares s JOIN expenses e ON e.id=s.expense_id "
        "WHERE e.group_id=?) ORDER BY u.id", (group_id, group_id, group_id)
    ).fetchall()
    # Net positive means the group owes this member; negative means they owe the group.
    nets = {m["id"]: 0 for m in members}
    for row in db.execute(
        "SELECT e.paid_by, e.amount_paise FROM expenses e WHERE e.group_id=? "
        "AND EXISTS (SELECT 1 FROM expense_shares s WHERE s.expense_id=e.id)", (group_id,)
    ):
        nets[row["paid_by"]] += row["amount_paise"]
    rows = db.execute(
        "SELECT s.user_id, s.share_paise FROM expenses e JOIN expense_shares s ON s.expense_id=e.id "
        "WHERE e.group_id=?", (group_id,)
    )
    for row in rows:
        nets[row["user_id"]] -= row["share_paise"]
    return members, nets


@app.get("/groups/{group_id}/balances")
def group_balances(group_id: int, db: sqlite3.Connection = Depends(get_db)):
    members, nets = _balances(group_id, db)
    names = {m["id"]: m["name"] for m in members}
    return {"group_id": group_id, "balances": [
        {"user_id": user_id, "name": names[user_id], "net": rupees(net),
         "owes": "group" if net < 0 else ("group is owed" if net > 0 else "settled")}
        for user_id, net in nets.items()
    ], "debts": [
        {"from_user_id": debtor, "from": names[debtor], "to_user_id": creditor,
         "to": names[creditor], "amount": rupees(amount)}
        for debtor, creditor, amount in settle(nets)
    ]}


@app.get("/groups/{group_id}/settlement-suggestions")
def settlement_suggestions(group_id: int, db: sqlite3.Connection = Depends(get_db)):
    """Product enhancement: consolidate all group debts into fewer suggested transfers."""
    members, nets = _balances(group_id, db)
    names = {m["id"]: m["name"] for m in members}
    transfers = settle(nets)
    return {"group_id": group_id, "suggestions": [
        {"from_user_id": debtor, "from": names[debtor], "to_user_id": creditor,
         "to": names[creditor], "amount": rupees(amount)}
        for debtor, creditor, amount in transfers
    ], "transfer_count": len(transfers)}


@app.get("/health")
def health():
    return {"status": "ok", "database": DATABASE_PATH}


@app.get("/workspace")
def workspace(group_id: int | None = None, db: sqlite3.Connection = Depends(get_db)):
   
    groups = [dict(row) for row in db.execute("SELECT id, name FROM groups ORDER BY id DESC")]
    users = [dict(row) for row in db.execute("SELECT id, name FROM users ORDER BY id")]
    dashboard = None
    if group_id is not None:
        group = require_group(db, group_id)
        members, nets = _balances(group_id, db)
        names = {member["id"]: member["name"] for member in members}
        balances = {
            "group_id": group_id,
            "balances": [
                {"user_id": user_id, "name": names[user_id], "net": rupees(net),
                 "owes": "group" if net < 0 else ("group is owed" if net > 0 else "settled")}
                for user_id, net in nets.items()
            ],
            "debts": [
                {"from_user_id": debtor, "from": names[debtor], "to_user_id": creditor,
                 "to": names[creditor], "amount": rupees(amount)}
                for debtor, creditor, amount in settle(nets)
            ],
        }
        transfers = settle(nets)
        dashboard = {
            "group": dict(group),
            "members": [dict(member) for member in members],
            "expenses": _list_expenses(group_id, db),
            "balances": balances,
            "settlement_suggestions": {
                "group_id": group_id,
                "suggestions": [
                    {"from_user_id": debtor, "from": names[debtor], "to_user_id": creditor,
                     "to": names[creditor], "amount": rupees(amount)}
                    for debtor, creditor, amount in transfers
                ],
                "transfer_count": len(transfers),
            },
        }
    return {"groups": groups, "users": users, "dashboard": dashboard}

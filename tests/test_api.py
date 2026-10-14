"""Route and business-logic tests using isolated in-memory SQLite databases."""

from decimal import Decimal
import sqlite3

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.database import SCHEMA, connect, initialize_database
from app.main import (
    ExpenseIn,
    ExpenseSplitIn,
    MemberIn,
    NameIn,
    add_member,
    create_expense,
    create_group,
    create_user,
    delete_group,
    group_balances,
    list_groups,
    list_expenses,
    list_members,
    list_users,
    remove_member,
    settlement_suggestions,
    workspace,
)


@pytest.fixture
def db():
    connection = connect(":memory:")
    connection.executescript(SCHEMA)
    try:
        yield connection
    finally:
        connection.close()


def user(db, name):
    return create_user(NameIn(name=name), db)


def group(db, name="Trip"):
    return create_group(NameIn(name=name), db)


def add_members(db, group_id, *users):
    for member in users:
        add_member(group_id, MemberIn(user_id=member["id"]), db)


def expense(db, group_id, payer, amount, description="Dinner"):
    return create_expense(group_id, ExpenseIn(
        paid_by=payer["id"], amount=Decimal(amount), description=description
    ), db)


def test_equal_expense_creation_history_and_balance(db):
    moh, rah, ank = (user(db, name) for name in ("Mohit", "Rahul", "Ankit"))
    trip = group(db, "Goa Trip")
    add_members(db, trip["id"], moh, rah, ank)

    created = expense(db, trip["id"], moh, "1200.00")
    assert created["amount"] == Decimal("1200.00")
    assert list_expenses(trip["id"], db)[0]["description"] == "Dinner"
    balances = group_balances(trip["id"], db)
    assert {(debt["from"], debt["to"], debt["amount"]) for debt in balances["debts"]} == {
        ("Rahul", "Mohit", Decimal("400.00")),
        ("Ankit", "Mohit", Decimal("400.00")),
    }
    assert sum(item["net"] for item in balances["balances"]) == Decimal("0.00")


def test_custom_expense_split_and_balance(db):
    payer, rahul, ankit = (user(db, name) for name in ("Mohit", "Rahul", "Ankit"))
    trip = group(db, "Goa Trip")
    add_members(db, trip["id"], payer, rahul, ankit)
    created = create_expense(trip["id"], ExpenseIn(
        paid_by=payer["id"], amount=Decimal("1000.00"), description="Hotel",
        splits=[
            ExpenseSplitIn(user_id=payer["id"], amount=Decimal("500.00")),
            ExpenseSplitIn(user_id=rahul["id"], amount=Decimal("300.00")),
            ExpenseSplitIn(user_id=ankit["id"], amount=Decimal("200.00")),
        ],
    ), db)

    assert created["split_type"] == "custom"
    assert [(share["name"], share["amount"]) for share in created["splits"]] == [
        ("Mohit", Decimal("500.00")), ("Rahul", Decimal("300.00")), ("Ankit", Decimal("200.00")),
    ]
    assert {(debt["from"], debt["to"], debt["amount"])
            for debt in group_balances(trip["id"], db)["debts"]} == {
        ("Rahul", "Mohit", Decimal("300.00")),
        ("Ankit", "Mohit", Decimal("200.00")),
    }
    assert list_expenses(trip["id"], db)[0]["split_type"] == "custom"


def test_custom_split_validation(db):
    payer, friend, outsider = (user(db, name) for name in ("Payer", "Friend", "Outsider"))
    trip = group(db)
    add_members(db, trip["id"], payer, friend)

    with pytest.raises(HTTPException) as invalid_total:
        create_expense(trip["id"], ExpenseIn(
            paid_by=payer["id"], amount=Decimal("10.00"), description="Lunch",
            splits=[ExpenseSplitIn(user_id=friend["id"], amount=Decimal("9.00"))],
        ), db)
    assert invalid_total.value.status_code == 422

    with pytest.raises(HTTPException) as invalid_member:
        create_expense(trip["id"], ExpenseIn(
            paid_by=payer["id"], amount=Decimal("10.00"), description="Lunch",
            splits=[ExpenseSplitIn(user_id=outsider["id"], amount=Decimal("10.00"))],
        ), db)
    assert invalid_member.value.status_code == 400

    with pytest.raises(ValidationError):
        ExpenseIn(
            paid_by=payer["id"], amount=Decimal("10.00"), description="Lunch",
            splits=[
                ExpenseSplitIn(user_id=friend["id"], amount=Decimal("5.00")),
                ExpenseSplitIn(user_id=friend["id"], amount=Decimal("5.00")),
            ],
        )


def test_custom_shares_are_preserved_when_membership_changes(db):
    payer, participant, newcomer = (user(db, name) for name in ("Payer", "Participant", "Newcomer"))
    trip = group(db)
    add_members(db, trip["id"], payer, participant)
    created = create_expense(trip["id"], ExpenseIn(
        paid_by=payer["id"], amount=Decimal("10.00"), description="Custom lunch",
        splits=[ExpenseSplitIn(user_id=participant["id"], amount=Decimal("10.00"))],
    ), db)

    add_member(trip["id"], MemberIn(user_id=newcomer["id"]), db)
    assert list_expenses(trip["id"], db)[0]["splits"] == created["splits"]
    remove_member(trip["id"], participant["id"], db)
    balances = group_balances(trip["id"], db)
    assert {item["name"] for item in balances["balances"]} == {"Payer", "Participant", "Newcomer"}
    assert [(debt["from"], debt["to"], debt["amount"]) for debt in balances["debts"]] == [
        ("Participant", "Payer", Decimal("10.00")),
    ]


def test_sub_rupee_remainder_is_conserved(db):
    a, b, c = (user(db, name) for name in ("A", "B", "C"))
    trip = group(db)
    add_members(db, trip["id"], a, b, c)
    expense(db, trip["id"], a, "0.01", "Tiny")
    balances = group_balances(trip["id"], db)["balances"]
    assert sum(item["net"] for item in balances) == Decimal("0.00")


def test_validation_for_amount_membership_and_duplicate_member(db):
    payer, outsider = user(db, "Payer"), user(db, "Outsider")
    trip = group(db)
    add_members(db, trip["id"], payer)
    with pytest.raises(HTTPException) as duplicate:
        add_member(trip["id"], MemberIn(user_id=payer["id"]), db)
    assert duplicate.value.status_code == 409
    with pytest.raises(HTTPException) as not_member:
        expense(db, trip["id"], outsider, "10.00")
    assert not_member.value.status_code == 400
    with pytest.raises(ValidationError):
        ExpenseIn(paid_by=payer["id"], amount="0", description="Lunch")
    with pytest.raises(HTTPException) as missing_group:
        group_balances(999, db)
    assert missing_group.value.status_code == 404


def test_later_member_is_included_in_existing_equal_splits(db):
    a, b, c = (user(db, name) for name in ("A", "B", "C"))
    trip = group(db)
    add_members(db, trip["id"], a, b)
    expense(db, trip["id"], a, "20.00", "Before C joined")
    add_member(trip["id"], MemberIn(user_id=c["id"]), db)
    debts = group_balances(trip["id"], db)["debts"]
    assert [(debt["from"], debt["to"], debt["amount"]) for debt in debts] == [
        ("B", "A", Decimal("6.67")),
        ("C", "A", Decimal("6.66")),
    ]


def test_startup_reconciles_expenses_saved_before_member_recalculation(tmp_path):
    path = str(tmp_path / "legacy.db")
    db = connect(path)
    legacy_schema = SCHEMA.replace(
        "    split_type TEXT NOT NULL DEFAULT 'equal' CHECK(split_type IN ('equal', 'custom')),\n", ""
    )
    assert legacy_schema != SCHEMA
    db.executescript(legacy_schema)
    users = [db.execute("INSERT INTO users(name) VALUES (?)", (name,)).lastrowid for name in ("A", "B", "C")]
    group_id = db.execute("INSERT INTO groups(name) VALUES ('Trip')").lastrowid
    db.executemany("INSERT INTO group_members(group_id, user_id) VALUES (?, ?)", [(group_id, user_id) for user_id in users])
    expense_id = db.execute(
        "INSERT INTO expenses(group_id, paid_by, amount_paise, description) VALUES (?, ?, 6000, 'Old expense')",
        (group_id, users[0]),
    ).lastrowid
    db.executemany(
        "INSERT INTO expense_shares(expense_id, user_id, share_paise) VALUES (?, ?, ?)",
        [(expense_id, users[0], 3000), (expense_id, users[1], 3000)],
    )
    db.commit()
    db.close()

    initialize_database(path)

    db = connect(path)
    split_type = db.execute("SELECT split_type FROM expenses WHERE id=?", (expense_id,)).fetchone()[0]
    shares = dict(db.execute(
        "SELECT user_id, share_paise FROM expense_shares WHERE expense_id=?", (expense_id,)
    ).fetchall())
    db.close()
    assert split_type == "equal"
    assert shares == {user_id: 2000 for user_id in users}


def test_settlement_suggestions_reduce_transfers(db):
    a, b, c = (user(db, name) for name in ("A", "B", "C"))
    trip = group(db)
    add_members(db, trip["id"], a, b, c)
    expense(db, trip["id"], a, "90.00", "Hotel")
    suggestions = settlement_suggestions(trip["id"], db)
    assert suggestions["transfer_count"] == 2
    assert {(item["from"], item["to"], item["amount"]) for item in suggestions["suggestions"]} == {
        ("B", "A", Decimal("30.00")), ("C", "A", Decimal("30.00"))
    }


def test_user_group_and_member_management(db):
    person = create_user(NameIn(name="  Sam  "), db)
    trip = group(db, "Weekend")
    add_members(db, trip["id"], person)
    assert person["name"] == "Sam"
    assert list_members(trip["id"], db) == [person]
    assert list_users(db) == [person]
    assert list_groups(db) == [trip]


def test_permanent_group_delete_removes_group_data_but_preserves_users(db):
    payer, friend = user(db, "Payer"), user(db, "Friend")
    trip = group(db, "Unused trip")
    add_members(db, trip["id"], payer, friend)
    expense(db, trip["id"], payer, "45.00", "Cab")

    result = delete_group(trip["id"], db)

    assert result == {"deleted": True, "group_id": trip["id"], "name": "Unused trip"}
    assert list_groups(db) == []
    assert list_users(db) == [payer, friend]
    assert db.execute("SELECT COUNT(*) FROM group_members WHERE group_id=?", (trip["id"],)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM expenses WHERE group_id=?", (trip["id"],)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM expense_shares").fetchone()[0] == 0

    with pytest.raises(HTTPException) as missing:
        delete_group(trip["id"], db)
    assert missing.value.status_code == 404


def test_permanent_group_delete_works_with_legacy_restrictive_foreign_keys():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE group_members (
            group_id INTEGER REFERENCES groups(id), user_id INTEGER REFERENCES users(id),
            PRIMARY KEY(group_id, user_id)
        );
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY, group_id INTEGER REFERENCES groups(id),
            paid_by INTEGER REFERENCES users(id), amount_paise INTEGER, description TEXT
        );
        CREATE TABLE expense_shares (
            expense_id INTEGER REFERENCES expenses(id), user_id INTEGER REFERENCES users(id),
            share_paise INTEGER, PRIMARY KEY(expense_id, user_id)
        );
        INSERT INTO users VALUES (1, 'Payer');
        INSERT INTO groups VALUES (1, 'Legacy group');
        INSERT INTO group_members VALUES (1, 1);
        INSERT INTO expenses VALUES (1, 1, 1, 1000, 'Old expense');
        INSERT INTO expense_shares VALUES (1, 1, 1000);
    """)

    assert delete_group(1, db)["deleted"] is True
    assert db.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM expense_shares").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM group_members").fetchone()[0] == 0
    db.close()


def test_remove_member_rebalances_existing_expenses_and_keeps_user_profile(db):
    payer, friend, removed = (user(db, name) for name in ("Payer", "Friend", "Removed"))
    trip = group(db, "Weekend")
    add_members(db, trip["id"], payer, friend, removed)
    expense(db, trip["id"], payer, "90.00", "Hotel")

    result = remove_member(trip["id"], removed["id"], db)

    assert result["removed"] is True
    assert [person["name"] for person in list_members(trip["id"], db)] == ["Payer", "Friend"]
    assert list_users(db) == [payer, friend, removed]
    debts = group_balances(trip["id"], db)["debts"]
    assert [(debt["from"], debt["to"], debt["amount"]) for debt in debts] == [
        ("Friend", "Payer", Decimal("45.00")),
    ]


def test_can_remove_last_member_and_clear_shares_until_someone_rejoins(db):
    payer = user(db, "Payer")
    trip = group(db, "Weekend")
    add_members(db, trip["id"], payer)
    expense(db, trip["id"], payer, "90.00", "Hotel")

    assert remove_member(trip["id"], payer["id"], db)["removed"] is True
    assert list_members(trip["id"], db) == []
    assert len(list_expenses(trip["id"], db)) == 1
    assert db.execute("SELECT COUNT(*) FROM expense_shares").fetchone()[0] == 0
    assert group_balances(trip["id"], db)["balances"] == []

    add_member(trip["id"], MemberIn(user_id=payer["id"]), db)
    assert db.execute("SELECT COUNT(*) FROM expense_shares").fetchone()[0] == 1
    assert group_balances(trip["id"], db)["balances"][0]["net"] == Decimal("0.00")


def test_workspace_snapshot_matches_saved_backend_data(db):
    payer, friend = user(db, "Payer"), user(db, "Friend")
    trip = group(db, "Weekend")
    add_members(db, trip["id"], payer, friend)
    expense(db, trip["id"], payer, "45.00", "Cab")

    snapshot = workspace(trip["id"], db)
    assert snapshot["groups"] == [trip]
    assert {person["id"] for person in snapshot["users"]} == {payer["id"], friend["id"]}
    assert snapshot["dashboard"]["group"] == trip
    assert snapshot["dashboard"]["expenses"][0]["description"] == "Cab"
    assert sum(row["net"] for row in snapshot["dashboard"]["balances"]["balances"]) == Decimal("0.00")
    assert snapshot["dashboard"]["settlement_suggestions"]["transfer_count"] == 1

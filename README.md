# Expense Sharing Service

A small FastAPI + SQLite backend for splitting group expenses.

## What it does

- Create users and groups, and manage group membership
- Record expenses, split equally by default or with custom amounts per person
- View expense history and each member's net balance
- **Enhancement:** settlement suggestions, a short list of transfers that clears everyone's balance
- Dashboard at `/` and interactive API docs at `/docs`

All money is stored in integer paise, so there's no floating-point drift.

## Quick start

Requires Python 3.10+ (3.12 recommended on Windows).

**Windows (PowerShell)**
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**macOS / Linux**
```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

**Docker**
```bash
docker compose up --build
```

Open http://localhost:8000. The health check is at `/health`. The database is saved as `expense_sharing.db` in the project folder; set `DATABASE_PATH` to change that.

**Broken venv on Windows?** (`ImpImporter` or `pydantic_core` errors) Stop Uvicorn, delete `.venv`, and recreate it with the commands above. If Windows won't let you delete it, just create a fresh one under a new name like `.venv-clean`.

## API

Amounts are in rupees with up to 2 decimals. IDs are integers. Errors follow FastAPI's standard `detail` format.

| Method | Path | Purpose |
|---|---|---|
| POST | `/users` | Create a user: `{"name":"Mohit"}` |
| GET | `/users` | List users |
| GET | `/workspace?group_id={id}` | One snapshot of users, groups, and the selected group's data |
| POST | `/groups` | Create a group |
| DELETE | `/groups/{group_id}` | Delete a group with its members, expenses, and shares |
| POST | `/groups/{group_id}/members` | Add a member: `{"user_id":2}` |
| GET | `/groups/{group_id}/members` | List members |
| DELETE | `/groups/{group_id}/members/{user_id}` | Remove a member (equal splits are recalculated) |
| POST | `/groups/{group_id}/expenses` | Add an expense |
| GET | `/groups/{group_id}/expenses` | Expense history |
| GET | `/groups/{group_id}/balances` | Net balance per member, plus debts |
| GET | `/groups/{group_id}/settlement-suggestions` | Suggested transfers |

**Equal split (default)**
```json
{"paid_by": 1, "amount": "1200.00", "description": "Dinner"}
```

**Custom split.** Shares must add up exactly to the total, and everyone must be a current member.
```json
{
  "paid_by": 1,
  "amount": "1000.00",
  "description": "Hotel",
  "splits": [
    {"user_id": 1, "amount": "500.00"},
    {"user_id": 2, "amount": "300.00"},
    {"user_id": 3, "amount": "200.00"}
  ]
}
```

**Balance example** (₹1,200 dinner, 3 members, Mohit paid): a positive net means the person gets money back, a negative net means they owe.
```json
{
  "group_id": 1,
  "balances": [
    {"user_id": 1, "name": "Mohit", "net": 800.0},
    {"user_id": 2, "name": "Rahul", "net": -400.0},
    {"user_id": 3, "name": "Ankit", "net": -400.0}
  ],
  "debts": [
    {"from": "Rahul", "to": "Mohit", "amount": 400.0},
    {"from": "Ankit", "to": "Mohit", "amount": 400.0}
  ]
}
```

## Settlement suggestions

On a trip, different people pay for different things, and you end up with a messy web of small paybacks. This feature nets everyone's balance and suggests a short list of transfers to clear it.

- It works from net balances, so expense shares remain the single source of truth.
- It uses deterministic greedy matching in integer paise.
- It is advice only and doesn't record payments.
- Greedy matching doesn't always find the absolute minimum number of transfers, and payment tracking is left for later.

## Custom splits (bonus)

Equal splits don't fit every expense, for example when someone skips the dinner. A custom split saves each person's share as a snapshot with the expense. If membership changes later, it isn't rewritten. The trade-off is that someone who has left the group can still owe money for an expense they joined.

## How it's built

- **Routes** (FastAPI) validate requests and coordinate the logic.
- **`money.py`** handles exact splitting and settlement math.
- **`database.py`** manages the SQLite schema and connections.

Tables: `users`, `groups`, `group_members` (composite key, so no duplicate membership), `expenses` (amount in paise, `split_type`), and `expense_shares` (per-person paise).

Foreign keys are on for every connection. Deleting a group removes its memberships and expenses, but user profiles stay. Removing a member only removes their membership. The dashboard asks for confirmation before both actions.

## Rules and assumptions

- The payer and all participants must be current group members, and a group needs at least one member before an expense can be added.
- The payer is credited the full amount, then every recorded share is subtracted, so a payer who is also a participant cancels out their own share.
- Totals must be positive with at most 2 decimals. Custom shares may be zero but must sum to the total.
- Equal-split leftover paise go to users in ascending ID order.
- Balances are per group. Expenses can't be edited or deleted.
- Adding or removing a member recalculates equal splits in one transaction. Custom shares stay with their original participants.
- If the last member is removed, expense history is kept and equal shares clear until someone joins.

## Tests

```bash
python -m pytest -q
```

Covers user, group, and member management, group deletion, equal and custom splits, balances, paise remainders, validation, membership changes, and settlement suggestions.

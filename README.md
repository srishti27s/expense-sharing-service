# Expense Sharing Service

A small Python + FastAPI backend for tracking equal-split group expenses. This is the Decentro SDE Intern assignment titled **Expense Sharing Service** (the supplied PDF filename refers to another assignment).

## Features

- Create and list users and groups.
- A hand-designed dashboard at `/` for groups, expenses, members, balances, and adding expenses. The interactive `/docs` API reference is still available for developers.
- Add users to groups and list current membership.
- Record an expense paid by a group member. The amount defaults to an equal split among current group members, including the payer; custom amounts are also supported.
- Choose an equal split (the default) or provide custom amounts for the members taking part in an expense.
- List a group's expense history and calculate current net balances.
- **Product enhancement: settlement suggestions.** This consolidates group net balances into a practical, deterministic set of payer-to-recipient transfers. It is advisory and does not mark debts as paid.
- Money is stored and calculated in integer paise to avoid floating-point drift.
- SQLite foreign keys and uniqueness constraints protect relationships and prevent duplicate membership.

## Requirements and local setup

Python 3.10 or newer is required. On Windows, Python 3.12 is recommended. Run these commands from this project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Using the virtual environment's Python executable directly avoids accidentally running a different Python installation. If Python 3.12 is not installed, replace `-3.12` with an installed version such as `-3.11`.

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

The dashboard is at `http://localhost:8000/`. Interactive API docs are at `/docs`, and the health check is `/health`. SQLite stores `expense_sharing.db` in the project directory by default, regardless of which folder starts Uvicorn. Set `DATABASE_PATH` to choose another file. The dashboard reads group data from a single workspace snapshot and refreshes when the page is revisited and every 15 seconds while visible.

### Recover a broken virtual environment on Windows

If installation reports errors such as `pkgutil has no attribute ImpImporter` or a `pydantic_core` import error, stop Uvicorn with `Ctrl+C`, close any other terminals using this project's `.venv`, then recreate it. From the project directory:

```powershell
deactivate
Remove-Item -Recurse -Force .venv
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

If `deactivate` is not recognized, continue with the next command. Recreate the environment whenever you switch Python versions; don't reuse a `.venv` created by another interpreter.

If Windows refuses to remove `.venv` because a Python file is still in use, leave that folder alone and use a new environment name instead:

```powershell
py -3.12 -m venv .venv-clean
.\.venv-clean\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-clean\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-clean\Scripts\python.exe -m uvicorn app.main:app --reload
```

## Docker

```bash
docker compose up --build
```

The API is available at `http://localhost:8000`; Compose keeps the database in the named `expense_data` volume. To stop the service, run `docker compose down`.

## API guide

All amounts in request and response JSON are rupees with up to two decimal places. IDs are integers. Errors use FastAPI's standard JSON `detail` response.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/users` | Create a user: `{"name":"Mohit"}` |
| `GET` | `/users` | List users |
| `GET` | `/workspace?group_id={id}` | Read users, groups, and an optional selected group's members, expenses, balances, and settlement suggestions in one snapshot |
| `POST` | `/groups` | Create a group: `{"name":"Goa Trip"}` |
| `DELETE` | `/groups/{group_id}` | Permanently delete a group, its memberships, expenses, and expense shares |
| `POST` | `/groups/{group_id}/members` | Add a member: `{"user_id":2}` |
| `GET` | `/groups/{group_id}/members` | List current group members |
| `DELETE` | `/groups/{group_id}/members/{user_id}` | Remove a member and recalculate the group's existing expense shares |
| `POST` | `/groups/{group_id}/expenses` | Add expense: `{"paid_by":1,"amount":"1200.00","description":"Dinner"}` |
| `GET` | `/groups/{group_id}/expenses` | List expense history |
| `GET` | `/groups/{group_id}/balances` | Return each member's net and the resulting debts |
| `GET` | `/groups/{group_id}/settlement-suggestions` | Return consolidated suggested transfers (enhancement) |

To set custom shares, include `splits` in the expense request. The amounts must add up exactly to the expense total, and each split user must be a current group member:

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

Leave out `splits` to use the default equal split. The dashboard has both options and shows the saved custom shares in expense history.

Example balance response for a ₹1,200 dinner shared by three members:

```json
{
  "group_id": 1,
  "balances": [
    {"user_id": 1, "name": "Mohit", "net": 800.0, "owes": "group is owed"},
    {"user_id": 2, "name": "Rahul", "net": -400.0, "owes": "group"},
    {"user_id": 3, "name": "Ankit", "net": -400.0, "owes": "group"}
  ],
  "debts": [
    {"from_user_id": 2, "from": "Rahul", "to_user_id": 1, "to": "Mohit", "amount": 400.0},
    {"from_user_id": 3, "from": "Ankit", "to_user_id": 1, "to": "Mohit", "amount": 400.0}
  ]
}
```

A positive `net` means the person should receive money; a negative `net` means they should pay. The `debts` list and enhancement endpoint render this as transfers between people.

## Product enhancement: settlement suggestions

I chose settlement suggestions because a trip can have many expenses paid by different people, which can otherwise leave the group with a long list of separate paybacks. The suggestions combine each member's net balance into a smaller, easier-to-follow set of transfers.

The service derives suggestions from current net balances instead of storing pairwise debts, so expense shares remain the source of truth. It uses deterministic greedy matching in integer paise and returns advice only; it does not claim a payment happened. This keeps the feature simple and auditable, but greedy matching is not guaranteed to produce the absolute minimum number of transfers, and actual settlement tracking is left for future work.

## Optional bonus: unequal expense splits

Equal sharing is not right for every purchase, so an expense can optionally provide an explicit share per participant. The API verifies membership, unique participants, and exact total conservation in paise; the dashboard lets the user enter each share. Custom shares are saved as an expense snapshot and are not rewritten when group membership changes. This supports cases like one person opting out, while keeping the original split auditable. The trade-off is that a former member can still owe money for a custom-split expense they originally joined.

## Architecture and database

FastAPI routes validate requests and coordinate use cases; `money.py` contains exact currency splitting and settlement logic; `database.py` manages SQLite schema and connections. SQLite is suitable for an assignment-sized, single-node service and avoids requiring an external database.

- `users` and `groups` hold the system's people and trips.
- `group_members` is a many-to-many relation with a composite primary key, so a person cannot join the same group twice.
- `expenses` stores the payer, group, description, timestamp, and amount in paise.
- `expenses.split_type` records whether an expense uses the default equal split or a custom split.
- `expense_shares` stores each expense's per-person split in paise. Adding or removing a member recalculates existing equal splits atomically; custom expense shares stay with their recorded participants.
- Removing the last member keeps expense history. Equal expense shares clear until someone joins again, while custom shares stay assigned to their original participants.

Foreign keys are enabled on each connection. Group deletion removes group-owned membership and expense history; user profiles are kept so they can remain in other groups or be reused. Removing a member only removes their group membership and keeps their profile. The dashboard confirms both actions before changing saved data.

## Business rules and assumptions

1. Only current group members can pay or share a new expense. A group needs at least one member before recording one.
2. The payer must be a current group member. Their share is credited for the full amount paid, then each recorded share is subtracted; if the payer is a participant, their own share cancels against that credit.
3. Expense totals must be positive and have at most two decimal places. Custom shares can be zero, but must total the expense amount. For equal splits, leftover paise are assigned in ascending user ID order.
4. A custom split must list unique current group members and sum exactly to the expense total. Members may be left out of a custom split; only recorded participants owe a share.
5. Balances are group-specific. Custom shares remain obligations for their original participants even if they later leave the group. Expenses are not edited or deleted through this API.
6. Settlement suggestions use deterministic greedy matching between net debtors and creditors. This reduces unnecessary back-and-forth, but is not guaranteed to find the globally smallest possible transfer count. Suggestions are advisory; payment recording is outside this version.

## Tests

Install dependencies, then from this directory run:

```bash
python -m pytest -q
```

Tests cover user/group/member management and removal, permanent group deletion, equal and custom expense splits, expense history, balances, paise remainders, validation, membership changes, and settlement suggestions.




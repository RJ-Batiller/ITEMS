# ITEMS
## A Web-Based Inventory Tracking and Equipment Management System for Mabalacat City College

This is the initial working foundation of the capstone system described in the paper.

### Stack
- Python / Flask
- MySQL
- HTML/CSS
- JavaScript
- QR-code module to be added in the next iteration

## Run on another Windows PC

The commands below assume Windows, PowerShell, XAMPP, and a fresh copy of the project.

### 1. Install prerequisites

Install these first:

- Python 3.11 or newer: https://www.python.org/downloads/
- XAMPP: https://www.apachefriends.org/
- Git, if you are cloning the project: https://git-scm.com/downloads

During Python installation, enable **Add Python to PATH**.

### 2. Get the project

If using Git:

```powershell
git clone <repository-url>
cd ITEMS_Capstone
```

If the project was copied by USB or ZIP, open PowerShell in the project folder instead.

### 3. Create the virtual environment and install packages

Run these commands from the project root:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For development and automated tests:

```powershell
pip install -r requirements-dev.txt
```

If PowerShell blocks activation, use the project interpreter directly instead of activating:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4. Configure `.env`

Create the local environment file:

```powershell
Copy-Item .env.example .env
notepad .env
```

Set at least these values in `.env`:

```dotenv
SECRET_KEY=use-a-long-random-secret-value
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=items_db
SUPERADMIN_USERNAME=superadmin1
SUPERADMIN_PASSWORD=choose-a-strong-password
SUPERADMIN_FULL_NAME=System Super Admin
SUPERADMIN_EMAIL=admin@mcc.edu.ph
APP_DEBUG=true
SESSION_COOKIE_SECURE=false
```

Do not commit `.env` or share its passwords and secret key.

### 5. Start MySQL and import the database

1. Open the XAMPP Control Panel.
2. Start **MySQL**.
3. Open `http://localhost/phpmyadmin`.
4. Import the project file `database.sql`.

You can also import from a terminal if the MySQL client is installed:

```powershell
mysql -u root -p < database.sql
```

If the XAMPP root account has no password, press Enter when prompted. If it has a password, set the same value in `DB_PASSWORD`.

### 6. Apply migrations and create the Super Admin

Use the project virtual-environment interpreter so the correct dependencies are used:

```powershell
.venv\Scripts\python.exe migrate.py
.venv\Scripts\python.exe seed_superadmin.py
```

The migration command adds any newer schema changes, including profile-picture support. The seed command creates or updates the Super Admin account using the values in `.env`.

### 7. Start Flask

```powershell
.venv\Scripts\python.exe app.py
```

Open the application at:

http://127.0.0.1:5000

Log in with `SUPERADMIN_USERNAME` and `SUPERADMIN_PASSWORD` from `.env`.

### 8. Run the tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

### Existing installation

If the database already exists, do not import `database.sql` again. Start MySQL, update `.env`, and run only:

```powershell
.venv\Scripts\python.exe migrate.py
.venv\Scripts\python.exe seed_superadmin.py
.venv\Scripts\python.exe app.py
```

### Troubleshooting

- If `py` is not recognized, reinstall Python with **Add Python to PATH** enabled.
- If PowerShell refuses `.venv\Scripts\Activate.ps1`, use `.venv\Scripts\python.exe` directly.
- If the app reports a missing `SECRET_KEY`, confirm that `.env` exists in the project root.
- If the app cannot connect to MySQL, confirm that XAMPP MySQL is running and that `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` match `.env`.
- If a schema column is missing, run `migrate.py` again.
- The schema does not create a default account until `seed_superadmin.py` is run.

### Current foundation
- Login/logout
- Role structure
- Dashboard
- Equipment records
- Categories
- Offices
- Search
- Archive action
- Transaction history
- User management foundation
- MySQL schema for accountability, QR codes, archives, and disposal

### Next implementation milestones
1. Finish authentication seed/admin creation
2. Add full CRUD for equipment
3. Add accountability assignment and transfer
4. Add QR-code generation and scanner
5. Add archive/disposal workflows
6. Add reports
7. Add role permissions per route
8. Add validation/security hardening
9. Add testing aligned with Chapter 3
10. Prepare deployment/demo build

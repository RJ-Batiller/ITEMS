# ITEMS
## A Web-Based Inventory Tracking and Equipment Management System for Mabalacat City College

This is the initial working foundation of the capstone system described in the paper.

### Stack
- Python / Flask
- MySQL
- HTML/CSS
- JavaScript
- QR-code module to be added in the next iteration

### 1. Install requirements

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

For development and testing, install the test tools as well:

```bash
pip install -r requirements-dev.txt
```

### 2. Create the database

Start MySQL through XAMPP, then open phpMyAdmin and import `database.sql`.

Alternatively:

```bash
mysql -u root -p < database.sql
```

### 3. Configure environment

Copy `.env.example` to `.env` and set your MySQL password if your XAMPP MySQL installation has one.

### 4. Seed the Super Admin

After importing the database, run:

```bash
python seed_superadmin.py
```

This creates or updates the default account:

- Username: `superadmin1`
- Password: the value of `SUPERADMIN_PASSWORD` in `.env`

You can override the defaults with `SUPERADMIN_USERNAME`, `SUPERADMIN_PASSWORD`, `SUPERADMIN_FULL_NAME`, and `SUPERADMIN_EMAIL` environment variables. `SUPERADMIN_PASSWORD` is required.

### 5. Start

```bash
python app.py
```

Open:

http://127.0.0.1:5000

The application reads `APP_DEBUG` from `.env` and defaults to debug disabled. Use `APP_DEBUG=true` only during local development. For production, use a strong `SECRET_KEY`, set `APP_DEBUG=false`, set `SESSION_COOKIE_SECURE=true` behind HTTPS, and run behind a production WSGI server. `SECRET_KEY` is required; the application will stop with a clear error if it is missing.

### 6. Test

```bash
python -m pytest -q
```

### Troubleshooting

- If `python` is not recognized on Windows, use `py` instead.
- If the application reports a missing `SECRET_KEY`, copy `.env.example` to `.env` and set a unique value.
- Confirm MySQL is running in XAMPP and that `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` match the local installation.
- After importing the schema, run `py seed_superadmin.py` to create or reset the Super Admin account.

### Important
The schema does not create a default account. Run `py seed_superadmin.py` after setting `SUPERADMIN_PASSWORD` in `.env`; the default development username is `superadmin1`.

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

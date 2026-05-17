# Retirement Planner Streamlit App

Local SQLite-backed retirement planning dashboard with:

- Roth conversion planning
- Tax-aware withdrawal projections
- Purchase planning and funding analysis
- Spending power / inflation modeling
- Dashboard scenario comparisons
- Social Security timing comparisons
- RMD projections
- Local backup and restore support

---

# Quick Start (Mac)

Double-click:

```text
Start Retirement App.command
```

The app will automatically launch in your browser.

---

# Quick Start (Linux)

```bash
./start_retirement_app.sh
```

---

# Manual Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the app:

```bash
streamlit run app.py
```

---

# Data Storage

Your personal data is stored locally in:

```text
retirement_planner.db
```

Database files and backups are intentionally excluded from git.

---

# Features

## Retirement Projection
- Multi-account retirement modeling
- Tax bucket tracking
- Inflation-adjusted retirement spending
- “Today’s dollars” purchasing-power mode
- Cash runway planning

## Roth Conversion Planning
- Fixed annual conversion scenarios
- Tax-bracket fill strategies
- Effective marginal bracket analysis
- Social Security interaction modeling
- Future RMD reduction estimates

## Purchase Planning
- Large purchase scenario modeling
- Tax-aware funding analysis
- Purchase impact visualization
- Withdrawal strategy comparison

## Dashboard & Visualization
- Interactive Plotly charts
- Bucket breakdowns
- Scenario comparisons
- Unified hover totals
- Purchase overlays
- Roth conversion windows

## Backup & Restore
- Automatic timestamped backups
- Manual backup downloads
- Upload and restore database backups

---

# Privacy

This repository contains only demo-safe defaults.

No personal financial data is included.

SQLite database files, backups, and local runtime files are excluded from version control.

---

# Recommended .gitignore

```gitignore
# Local data
*.db
*.sqlite
*.sqlite3

# Backup folders
backups/
restored_uploads/

# Python
__pycache__/
*.pyc
.venv/
venv/

# Streamlit
.streamlit/secrets.toml

# macOS
.DS_Store
```

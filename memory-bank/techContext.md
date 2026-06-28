# Technical Context

## Tech Stack
*   **Language:** [e.g., TypeScript, Python, Go]
*   **Framework:** [e.g., Node.js, FastAPI, Gin]
*   **Database:** [e.g., SQLite, PostgreSQL]
*   **Testing:** [e.g., Jest, Pytest]

## Development Environment
*   **System Constraints:**
    *   Local environment must execute via Docker or matching local runtime versions.
    *   Database migrations must be executed sequentially and stored in source control.
*   **Required Commands:**
    *   **Dependency Installation:** `[e.g., npm install or pip install -r requirements.txt]`
    *   **Start Local Dev Server:** `[e.g., npm run dev or uvicorn main:app --reload]`
    *   **Run Test Suite:** `[e.g., npm test or pytest]`
    *   **Run Linter/Formatter:** `[e.g., npm run lint or black .]`

## Database Schema (Initial Draft)
*   `users`: `id (uuid)`, `email (text)`, `password_hash (text)`, `created_at (timestamp)`
*   `tasks`: `id (uuid)`, `user_id (uuid, foreign key)`, `title (text)`, `is_completed (boolean)`, `created_at (timestamp)`
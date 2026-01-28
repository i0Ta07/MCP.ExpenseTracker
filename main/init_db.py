import psycopg2,sys,os
from dotenv import load_dotenv

load_dotenv()

# For this to work each user has to configure the Postgres on their machine.
# Better to ship a Docker container with PostgreSQL and your MCP server preconfigured.

def get_conn():
    try:
        conn = psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"), 
            password=os.getenv("DB_PASSWORD"),
            host="localhost",
            port=5432
        )
        print("Database connection successful", file=sys.stderr)
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}", file=sys.stderr)
        raise

def init_schema():
    conn = get_conn()
    cur = conn.cursor()


    # Users table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY,
        email TEXT NOT NULL UNIQUE CHECK (email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """)

    # Initialize Local User
    cur.execute("""
        INSERT INTO users (id, email)
        VALUES (
            '00000000-0000-0000-0000-000000000001',
            'local@expense.tracker'
        )
        ON CONFLICT (id) DO NOTHING;
    """)

    # Expenses table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id SERIAL PRIMARY KEY,
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expense_date DATE NOT NULL,
        amount NUMERIC(10,2) NOT NULL CHECK (amount > 0),
        category TEXT NOT NULL,
        subcategory TEXT,
        description TEXT,
        currency CHAR(3) NOT NULL DEFAULT 'INR',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                
    );
    """)

    # Index for faster queries
    cur.execute("""     
    CREATE INDEX IF NOT EXISTS idx_expenses_user_category
    ON expenses (user_id, category);
                
    CREATE INDEX IF NOT EXISTS idx_expenses_user_date
    ON expenses (user_id, expense_date);
                
    CREATE INDEX IF NOT EXISTS idx_expenses_user_date_category
    ON expenses (user_id, expense_date, category);
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("Schema initialized successfully.")


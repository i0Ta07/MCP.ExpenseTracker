import sys,os
from fastmcp import FastMCP
from datetime import date,datetime
from typing import  Optional
from pydantic import BaseModel,Field
import requests
from init_db import get_conn

# Add startup logging
print("=== Expense Tracker MCP Server Starting ===", file=sys.stderr)
print(f"Python path: {sys.executable}", file=sys.stderr)
print(f"Working directory: {os.getcwd()}", file=sys.stderr)

mcp = FastMCP("Expense Tracker")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

def  get_default_user_id()-> str:
    return '00000000-0000-0000-0000-000000000001'

allowed_currencies = ['INR','AED','CAD','EUR','MYR','SEK','USD','AUD','CHF','GBP','JPY','PHP','SGD','ZAR','BRL','CNY','HKD','MXN','SAR','THB']

@mcp.tool()
def add_expense(expense_date:str,amount:float,category:str,subcategory:str = "",description:str = "",currency:str = "INR"):
    """Add an expense to the database"""

    expense_date = expense_date.strip()
    category = category.strip().lower() # In future easy to look up using category and subcategory if they are in lower case
    subcategory = subcategory.strip().lower()
    description = description.strip()
    currency = currency.strip().upper()

    user_id = get_default_user_id()
    conn = get_conn()
    cur = conn.cursor()
    try:
        parsed_date = datetime.strptime(expense_date, "%Y-%m-%d").date()
    except ValueError:
        result = {
            "status": "error",
            "error": "Invalid date format. Use YYYY-MM-DD"
        }
     
    if parsed_date > date.today():
        result = {
            "status": "error",
            "error": "Future dates are not allowed"
        }

    # Perform Data Validation
    if amount <= 0:
        result =  {
            "status": "error",
            "error": "Amount must be greater than zero"
        }    
    if category == '':
        result = {
            "status": "error",
            "error": "Category cannot be empty"            
        }
    if currency not in allowed_currencies :
        result = {
            "status":"error",
            "error":"Currency can only be 3 letters like INR,USD etc or given currency is not supported"
        }
    try:
        cur.execute(
            """
            INSERT INTO expenses (user_id, expense_date, amount, category, subcategory, description, currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, expense_date, amount, category, subcategory, description, currency)
        )

        expense_id = cur.fetchone()[0] # Fetchone fetches only a single row from the result, since here the result only has one row it looks like (42,) and when [0] gets you the first column so we get 42 
        conn.commit()

        result = {
            "status": "success",
            "result":{"id": expense_id}
        }

    except Exception as e:
        conn.rollback()
        result = {
            "status": "error",
            "error": str(e)
    }
    cur.close()
    conn.close()
    return result


@mcp.tool()
def list_categories(subcategories:bool = False)-> dict:
    """
    List all the categories and subcategories inside the database
    """
    user_id = get_default_user_id()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT category, subcategory FROM expenses WHERE user_id = %s ORDER BY category, subcategory;",
        (user_id,)
    )
    data = cur.fetchall()
    if subcategories:
        categories = {}
        for entry in data:
            category, subcat = entry
            if category not in categories:
                # First time seeing this category
                categories[category] = [subcat] if subcat else []
            else:
                # Category already exists
                if subcat and subcat not in categories[category]:
                    categories[category].append(subcat)
    else:
        categories = sorted(set(row[0] for row in data)) # flatten list: [('Food',), ('Travel',), ('Rent',)] to ['Food', 'Travel', 'Rent']
    
    cur.close()
    conn.close()
    return {
        "status": "success",
        "result":{"categories":categories}
    }



class FiltersSchema(BaseModel):
    amount_min:Optional[float] = Field(description="Minimum Amount  filter",default=None, ge=0)
    amount_max:Optional[float] = Field(description="Maximum Amount filter",default=None,ge=0)
    category:Optional[str] = Field(description="Category filter",default=None)
    subcategory:Optional[str] = Field(description="Subcategory filter",default=None)
    expense_date_from:Optional[str] = Field(description="Start Date filter in format YYYY-MM-DD",default=None)
    expense_date_to:Optional[str] = Field(description="End Date filter in format YYYY-MM-DD",default=None)
    currency:Optional[str] = Field(description="Currency filter",default=None)


@mcp.tool()
def list_expenses(filters: FiltersSchema):
    """
    Fetch candidate expense records for listing, update, or deletion.
    """
    # Strip
    if filters.category:
        filters.category = filters.category.strip().lower()
    if filters.subcategory:
        filters.subcategory = filters.subcategory.strip().lower()
    if filters.currency:
        filters.currency = filters.currency.strip().upper()
    if filters.expense_date_from:
        filters.expense_date_from = filters.expense_date_from.strip()
    if filters.expense_date_to:
        filters.expense_date_to = filters.expense_date_to.strip()
    
    if (filters.amount_max and filters.amount_min):
        if (filters.amount_min < 0) or (filters.amount_max < 0):
            return {
                "status":"error",
                "error":"Amount cannot be less than zero."
            }
        if (filters.amount_min > filters.amount_max):
            return {
                "status":"error",
                "error":"Minimum amount cannot be larger than maximum amount"
            }
    
    if filters.expense_date_to and filters.expense_date_from:
        try:
            parsed_expense_date_to = datetime.strptime(filters.expense_date_to , "%Y-%m-%d").date()
            parsed_expense_date_from = datetime.strptime(filters.expense_date_from , "%Y-%m-%d").date()
            if parsed_expense_date_from > parsed_expense_date_to:
                return {
                "status": "error",
                "error": "expense_date_to cannot be smaller than expense_date_from"
            }
        except ValueError:
            return {
            "status": "error",
            "error": "Invalid date format. Use YYYY-MM-DD"
        }
    
    user_id = get_default_user_id()
    conn = get_conn()
    cur = conn.cursor()

    base_query = """
        SELECT id, expense_date, amount, category, subcategory, description, currency
        FROM expenses
        WHERE user_id = %s
    """
    conditions = []
    params = [user_id]

    if filters.amount_min is not None:
        conditions.append("amount >= %s")
        params.append(filters.amount_min)

    if filters.amount_max is not None:
        conditions.append("amount <= %s")
        params.append(filters.amount_max)

    if filters.category:
        conditions.append("category = %s")
        params.append(filters.category)

    if filters.subcategory:
        conditions.append("subcategory = %s")
        params.append(filters.subcategory)

    if filters.expense_date_from:
        conditions.append("expense_date >= %s")
        params.append(filters.expense_date_from)

    if filters.expense_date_to:
        conditions.append("expense_date <= %s")
        params.append(filters.expense_date_to)
    
    if filters.currency in allowed_currencies:
        conditions.append("currency = %s")
        params.append(filters.currency)

    if conditions:
        base_query += " AND " + " AND ".join(conditions)

    base_query += " ORDER BY expense_date DESC, amount DESC"

    cur.execute(base_query, tuple(params))
    rows = cur.fetchall()
    records = []
    columns = [desc[0] for desc in cur.description]
    for row in rows:
        records.append(dict(zip(columns,row)))


    cur.close()
    conn.close()

    return {
        "status": "success",
        "result":{
            "count": len(records),
            "records": records
        }
    }

    
@mcp.tool()
def update_expense(expense_id:int,updates:dict):
    """ Update an expense"""
    update_dict = {}
    allowed_fields = ['expense_date','amount','category','subcategory','description','currency']
    for key,value in updates.items():
        if key in allowed_fields:
            if key == 'expense_date' and type(value) == str:
                value = value.strip()
                try:
                    parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
                except ValueError:
                    return {
                        "status": "error",
                        "error": "Invalid date format. Use YYYY-MM-DD in string format"
                    }  
                if parsed_date > date.today():
                    return {
                        "status": "error",
                        "error": "Future dates are not allowed"
                    }
                update_dict[key] = value
            elif key  == 'amount':
                try:
                    value= float(value)
                except ValueError:
                    return{
                        "status": "error",
                        "error": "Amount can only be float"                        
                    }
                update_dict[key] = value
            elif key in ['category','subcategory','description']:
                if type(value) == str and value != '':
                    update_dict[key] = value.strip().lower()
                else:
                    return {
                        "status":"error",
                        "error":"Updates in category, subcategory and description only expects non-empty strings"
                    }
            elif key == 'currency' :
                if value in allowed_currencies:
                    update_dict[key] = value.strip().upper()
                else: 
                    return {
                    "status":"error",
                    "error":"Currency can only be 3 letters like INR,USD etc or given currency is not supported"
                }
        else:
            return {
                "status":"error",
                "error":"You can only edit expense_date, amount, category, subcategory, description and currency"
            }
    user_id = get_default_user_id()

    if not update_dict:
        return {
            "status": "error",
            "error": "No valid fields to update"
        }

    set_clauses = []
    params = []

    for column, value in update_dict.items():
        set_clauses.append(f"{column} = %s")
        params.append(value)

    query = f"""
        UPDATE expenses
        SET {', '.join(set_clauses)}
        WHERE id = %s AND user_id = %s
    """

    params.extend([expense_id, user_id])

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    conn.commit()

    rows_affected = cur.rowcount

    cur.close()
    conn.close()

    return {
        "status": "success",
        "result":{
            "rows_affected": rows_affected
        }
    }


@mcp.tool()
def delete_expense(expense_id:int):
    """Delete an expense from database"""
    user_id = get_default_user_id()
    conn = get_conn()
    cur = conn.cursor()
    query = """
    DELETE FROM expenses WHERE user_id = %s AND id = %s;
    """
    cur.execute(query, (user_id,expense_id))
    rows_affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if rows_affected == 0:
        return {"status":"error", "error":"No such expense record exists"}
    else:
        return {"status":"success", "result":{"rows_affected": rows_affected}}

@mcp.tool()
def get_expense(expense_id:int):
    """Get an expense by id"""
    user_id = get_default_user_id()
    conn = get_conn()
    cur = conn.cursor()
    query = """
    SELECT expense_date,amount,category,subcategory,description,currency FROM expenses WHERE user_id = %s AND id = %s;
    """
    cur.execute(query, (user_id,expense_id))
    row = cur.fetchone() # Get the entire row
    cur.close()
    conn.close()
    if row:
        columns = [desc[0] for desc in cur.description] # (('expense_date', type_code, size...), ('amount', ...), ('category', ...))
        record = dict(zip(columns, row))
        return {
            "status": "success",
            "result":{
                "expense_record": record
            }
        }   
    else:
        return {
            "status": "error",
            "error":"No such expense record exists"
        } 

@mcp.tool()
def convert_currency(amount:float, target_currency:str,base_currency:str = 'INR'):
    """Convert an amount from a base currency to a target currency."""
    target_currency = target_currency.strip().upper()
    base_currency = base_currency.strip().upper()
    if target_currency not in allowed_currencies or base_currency not in allowed_currencies:
        return {
            "status":"error",
            "error":"Currency not supported"
        }

    if amount <= 0:
        return {
            "status":"error",
            "error":"Amount cannot be equal to less than zero"            
        }
    try:
        response = requests.get(f"https://open.er-api.com/v6/latest/{base_currency}",timeout=5)
    except requests.exceptions.RequestException:
        return{
            "status":"error",
            "result":"Request Failed"
        }
    if response.status_code != 200:
        return {"status":"error", "error":"Bad Request"}

    data = response.json()
    try:
        rate = data["rates"][target_currency]
    except KeyError:
        return {"status":"error", "error":f"Currency rate for {target_currency} not found"}
    result = round(rate * amount,2)
    return {
        "status":"success",
        "result":result
    }


@mcp.resource("expense://categories",mime_type="application/json")
def categories():
    # Read fresh each time so you can edit the file without restarting
    with open(CATEGORIES_PATH,"r",encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    mcp.run()

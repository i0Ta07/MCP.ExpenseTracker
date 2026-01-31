from decimal import Decimal,ROUND_HALF_UP,InvalidOperation
import sys,os
from fastmcp import FastMCP
from datetime import date,datetime
from typing import  Optional,Literal,TypedDict
from pydantic import BaseModel,Field
import requests
from init_db import get_conn,init_schema

init_schema()

# Add startup logging
# print("=== Expense Tracker MCP Server Starting ===", file=sys.stderr)
# print(f"Python path: {sys.executable}", file=sys.stderr)
# print(f"Working directory: {os.getcwd()}", file=sys.stderr)

mcp = FastMCP("Expense Tracker")

# Choose the base currency from 'INR','AED','CAD','EUR','MYR','SEK','USD','AUD','CHF','GBP','JPY','PHP','SGD','ZAR','BRL','CNY','HKD','MXN','SAR','THB'
BASE_CURRENCY = "INR"

CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

def  get_default_user_id()-> str:
    return '00000000-0000-0000-0000-000000000001'

# All the aggreagtion operations will be done on in base_amount. You can perform aggregation on original_amount only when grouped in a particular currency.
class AddExpenseSchema(BaseModel):
    expense_date:str = Field(description = "Date of expense in format YYYY-MM-DD")
    original_amount:Decimal = Field(description = "Expense amount in the currency given by user.",max_digits=10, decimal_places=2, gt=0)
    category:str = Field(description = "Category of the expense")
    subcategory:Optional[str] = Field(description = "Subcategory of the expense", default=None)
    description:Optional[str] = Field(description = "Description of the expense",default=None)
    currency:Literal['INR','AED','CAD','EUR','MYR','SEK','USD','AUD','CHF','GBP','JPY','PHP','SGD','ZAR','BRL','CNY','HKD','MXN','SAR','THB'] = Field(description = "Currency of the original_amount given by the user")

@mcp.tool()
def add_expense(expense : AddExpenseSchema):
    """Add an expense to the database"""

    user_id = get_default_user_id()
    columns = [ "user_id","original_amount", "currency"]
    params = [ user_id,expense.original_amount,expense.currency]

    if not expense.expense_date.strip():
        raise ValueError("Date cannot be empty.")
    else:
        expense.expense_date = expense.expense_date.strip()
        try:
            parsed_date = datetime.strptime(expense.expense_date, "%Y-%m-%d").date()
        except ValueError as e:
            raise ValueError("Invalid date format. Use YYYY-MM-DD") from e # New exception from e
        if parsed_date > date.today():
            raise ValueError("Future dates are not allowed")
        columns.append("expense_date")
        params.append(expense.expense_date)

        if expense.subcategory:
            if not expense.subcategory.strip():
                raise ValueError("Subcategory cannot be an empty string.")                
            columns.append("subcategory")
            params.append(expense.subcategory.strip().lower())            

    if expense.description:
        if not expense.description.strip():
            raise ValueError("Desciption cannot be an empty string.") 
        else:
            columns.append("description")
            params.append(expense.description.strip().lower())   

    # Perform Data Validation
    if expense.currency != BASE_CURRENCY:
        try:
            response = convert_currency(expense.original_amount,expense.currency,BASE_CURRENCY)
        except Exception as e:
            raise RuntimeError((f"Currency conversion failed: {e}"))

        base_amount = response["result"]
    else:
        base_amount = expense.original_amount
    columns.append("base_amount")
    params.append(base_amount)

    if not expense.category.strip():
        raise ValueError("Category cannot be empty" )
    columns.append("category")
    params.append(expense.category.strip().lower()) # In future easy to look up using category and subcategory if they are in lower case

    placeholders = ", ".join(["%s"] * len(columns))
    query = f"""
        INSERT INTO expenses ({", ".join(columns)})
        VALUES ({placeholders})
        RETURNING id
    """

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                expense_id = cur.fetchone()[0] # Fetchone fetches only a single row from the result, since here the result only has one row it looks like (42,) and when [0] gets you the first column so we get 42 
                return {
                    "status": "success",
                    "result":{"id": expense_id}
                }
    except Exception as e:
        raise RuntimeError("Failed to create expense") from e


@mcp.tool()
def list_categories(subcategories:bool = False)-> dict:
    """
    List all the categories and subcategories inside the database
    """
    user_id = get_default_user_id()
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT category, subcategory FROM expenses WHERE user_id = %s ORDER BY category, subcategory;",
                    (user_id,)
                )
                data = cur.fetchall()
    except Exception as e:
        raise RuntimeError("Failed to create expense") from e
    if not data:
        return{
        "status": "success",
        "result":"No categories."            
        }
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
    return {
        "status": "success",
        "result":{"categories":categories}
    }


class FiltersSchema(BaseModel):
    min_amount:Optional[float] = Field(description="Minimum Amount  filter",default=None, gt=0)
    max_amount:Optional[float] = Field(description="Maximum Amount filter",default=None,gt=0)
    category:Optional[str] = Field(description="Category filter",default=None)
    subcategory:Optional[str] = Field(description="Subcategory filter",default=None)
    start_date:Optional[str] = Field(description="Start Date filter in format YYYY-MM-DD",default=None)
    end_date:Optional[str] = Field(description="End Date filter in format YYYY-MM-DD",default=None)
    currency:Optional[Literal['INR','AED','CAD','EUR','MYR','SEK','USD','AUD','CHF','GBP','JPY','PHP','SGD','ZAR','BRL','CNY','HKD','MXN','SAR','THB']] = Field(description="Currency filter",default=None)

@mcp.tool()
def list_expenses(filters: FiltersSchema):
    """
    Fetch candidate expense records for listing, update, or deletion.
    """
    
    if (filters.max_amount and filters.min_amount) and (filters.min_amount > filters.max_amount):
            raise ValueError("Minimum amount cannot be larger than maximum amount")
    
    if filters.end_date and filters.start_date:
        filters.start_date = filters.start_date.strip()
        filters.end_date = filters.end_date.strip()
        try:
            parsed_end_date = datetime.strptime(filters.end_date , "%Y-%m-%d").date()
            parsed_start_date = datetime.strptime(filters.start_date , "%Y-%m-%d").date()
            if parsed_start_date > parsed_end_date:
                raise ValueError("End date cannot be smaller than Start date")
        except ValueError as e:
            raise ValueError("Invalid date format. Use YYYY-MM-DD") from e
    
    user_id = get_default_user_id()

    base_query = """
        SELECT id, expense_date, original_amount, base_amount, category, subcategory, description, currency
        FROM expenses
        WHERE user_id = %s
    """
    conditions = []
    params = [user_id]

    if filters.min_amount is not None:
        conditions.append("base_amount >= %s")
        params.append(filters.min_amount)

    if filters.max_amount is not None:
        conditions.append("base_amount <= %s")
        params.append(filters.max_amount)

    # Search category and subcategory in lower cases.
    if filters.category:
        conditions.append("category = %s")
        params.append(filters.category.strip().lower())

    if filters.subcategory:
        conditions.append("subcategory = %s")
        params.append(filters.subcategory.strip().lower())

    if filters.start_date:
        conditions.append("expense_date >= %s")
        params.append(filters.start_date)

    if filters.end_date:
        conditions.append("expense_date <= %s")
        params.append(filters.end_date)
    
    if filters.currency:
        conditions.append("currency = %s")
        params.append(filters.currency)
    
    if len (params) == 1:
        raise RuntimeError("Need atleast 1 filter to list expenses")

    if conditions:
        base_query += " AND " + " AND ".join(conditions)

    base_query += " ORDER BY expense_date DESC, base_amount DESC"

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(base_query, tuple(params))
                rows = cur.fetchall()
                columns = [desc[0] for desc in cur.description]
    except Exception as e:
        raise RuntimeError("Failed to fetch expenses") from e
    
    if not rows:
        return {
            "status": "success",
            "result": {
                "count": 0,
                "records": []
            }
        }

    records = []
    for row in rows:
        records.append(dict(zip(columns,row)))

    return {
        "status": "success",
        "result":{
            "count": len(records),
            "records": records
        }
    }

# Inherit Insert Schema
class ExpenseUpdateSchema(AddExpenseSchema):
    expense_date: Optional[str] = Field(description = "Date of expense in format YYYY-MM-DD",default=None)
    original_amount: Optional[Decimal] = Field(description = "Expense amount in the currency given by user.",max_digits=10, decimal_places=2, gt=0,default=None)
    category: Optional[str] = Field(description = "Category of the expense",default=None)
    currency: Optional[
        Literal['INR','AED','CAD','EUR','MYR','SEK','USD','AUD','CHF','GBP',
                'JPY','PHP','SGD','ZAR','BRL','CNY','HKD','MXN','SAR','THB']
    ] = Field(description = "Currency of the original_amount given by the user",default=None)

@mcp.tool()
def update_expense(expense_id: int, data: ExpenseUpdateSchema):
    """ Update an expense """
    update_dict = {}
    allowed_fields = ['expense_date','original_amount','category','subcategory','description','currency']
    updates = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None} # Only include fields that are not null.
    update_fields = list(set(allowed_fields) & set(updates.keys()))
    user_id = get_default_user_id()
    if len(update_fields) == 0:
        raise RuntimeError("Either the updates dict is empty or given columns cannot be updated.")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {', '.join(update_fields)} FROM expenses WHERE id = %s AND user_id = %s",(expense_id,user_id))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("No such record exists")
    except Exception as e:
        raise RuntimeError("Failed to fetch record") from e
    db_record = dict(zip(update_fields, row))
    for column in update_fields:
            value = updates[column]
            db_value = db_record[column]
            if column == 'expense_date':
                if value.strip():
                    value = value.strip()
                    if value == db_value:
                        raise RuntimeError("Both stored and new date are same")
                    try:
                        parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
                    except ValueError as e:
                        raise ValueError("Invalid date format. Use YYYY-MM-DD in string format") from e
                    if parsed_date > date.today():
                        raise ValueError("Future dates are not allowed")
                    update_dict[column] = value
                else:
                    raise TypeError("Date can only be non empty string in YYYY-MM-DD format.")
            elif column in ['category','subcategory','description']:
                if value.strip():
                    value = value.strip().lower()
                    if value == db_value:
                        raise RuntimeError(f"Both stored and new {column} are same")
                    update_dict[column] = value
                else:
                    raise TypeError(f"{column} can only be a string")               
            elif column  == 'original_amount' or column == 'currency':
                if 'currency' not in update_fields:
                    raise RuntimeError("You cannot update amount without provding the currency in updates")
                elif 'original_amount' not in update_fields:
                    raise RuntimeError("You cannot update currency without provding the original amount in updates" )
                 
                try:
                    new_amount= Decimal(updates["original_amount"])
                except InvalidOperation as e:
                    raise InvalidOperation ("Amount can only be Decimal") from e
                new_currency = updates['currency']
                db_amount = db_record['original_amount']
                db_currency = db_record['currency']
                amount_changed = new_amount != db_amount
                currency_changed = new_currency != db_currency
                if not amount_changed and not currency_changed:
                    raise RuntimeError("Both currency and amount are same as the values in the database")
                if amount_changed:
                    update_dict['original_amount'] = new_amount

                if currency_changed:
                    update_dict['currency'] = new_currency

                if amount_changed or currency_changed:
                    effective_currency = new_currency
                    effective_amount = new_amount

                    if effective_currency != BASE_CURRENCY:
                        try:
                            response = convert_currency(effective_amount, effective_currency, BASE_CURRENCY)
                        except Exception as e:
                            raise RuntimeError(f"Currency conversion failed: {e}")
                        update_dict["base_amount"] = response["result"]
                    else:
                        update_dict["base_amount"] = effective_amount
    set_clauses = []
    params = []

    if not update_dict:
        raise RuntimeError("No changes detected")

    for column, value in update_dict.items():
        set_clauses.append(f"{column} = %s")
        params.append(value)

    query = f"""
        UPDATE expenses
        SET {', '.join(set_clauses)}
        WHERE id = %s AND user_id = %s
    """
    params.extend([expense_id, user_id])

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows_affected = cur.rowcount
    except Exception as e:
        raise RuntimeError("Update Failed") from e
    
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

    query = "DELETE FROM expenses WHERE user_id = %s AND id = %s;"
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,expense_id))
                rows_affected = cur.rowcount
    except Exception as e:
        raise RuntimeError("Deletion failed") from e
    if rows_affected == 0:
        raise RuntimeError("No such expense record exists")
    else:
        return {"status":"success", "result":{"rows_affected": rows_affected}}

@mcp.tool()
def get_expense(expense_id:int):
    """Get an expense by id"""
    user_id = get_default_user_id()
    query = "SELECT expense_date,base_amount,original_amount, category,subcategory,description,currency FROM expenses WHERE user_id = %s AND id = %s;"

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (user_id,expense_id))
                row = cur.fetchone() # Get the entire row
    except Exception as e:
        raise RuntimeError("Runtime error, cannot fetch expense")
    if row:
        columns = [desc[0] for desc in cur.description] # (('expense_date', type_code, size...), ('base_amount', ...), ('category', ...))
        record = dict(zip(columns, row))
        return {
            "status": "success",
            "result":{
                "expense_record": record
            }
        }   
    else:
        raise RuntimeError("No such expense record exists")


def convert_currency(amount:Decimal, base_currency:str, target_currency:str):
    """Convert an amount from a target currency to a base currency."""
    try:
        response = requests.get(f"https://open.er-api.com/v6/latest/{base_currency}",timeout=5)
    except requests.exceptions.RequestException as r:
        raise requests.exceptions.RequestException("Request Failed")

    if response.status_code != 200:
        raise RuntimeError("Bad Request")

    data = response.json()
    try:
        rate = data["rates"][target_currency]
    except KeyError:
        raise KeyError(f"Currency rate for {target_currency} not found")
    result = (Decimal(rate) * amount).quantize(Decimal("0.00"), rounding=ROUND_HALF_UP)
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

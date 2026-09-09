from datetime import date
import os, sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv('FINANCE_DB_PATH', ROOT / 'backend' / 'finance_demo.db'))
app = FastAPI(title='个人理财助手 API', version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

class AccountIn(BaseModel):
    account_name: str = Field(min_length=1, max_length=50)
    account_type: str = '银行卡'
    balance: float = Field(ge=0)

class TransactionIn(BaseModel):
    account_id: int
    category: str = '其他'
    trans_type: str
    amount: float = Field(gt=0)
    remark: str = ''
    trans_date: str = date.today().isoformat()

def db():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, balance REAL NOT NULL); CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL, category TEXT NOT NULL, type TEXT NOT NULL, amount REAL NOT NULL, remark TEXT, trans_date TEXT NOT NULL);')
    if conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0] == 0:
        conn.executemany('INSERT INTO accounts(name,type,balance) VALUES(?,?,?)', [('招商银行','银行卡',12680),('微信零钱','电子钱包',1860),('现金','现金',420)])
        conn.executemany('INSERT INTO transactions(account_id,category,type,amount,remark,trans_date) VALUES(?,?,?,?,?,?)', [(1,'工资','收入',15000,'本月工资',date.today().replace(day=1).isoformat()),(1,'餐饮','支出',32,'午餐',date.today().isoformat()),(2,'交通','支出',18,'地铁',date.today().isoformat()),(1,'住房','支出',3200,'房租',date.today().replace(day=2).isoformat())]); conn.commit()
    return conn

@app.get('/api/health')
def health(): return {'status':'ok','mode':'sqlite-demo'}

@app.get('/api/overview')
def overview():
    c=db(); month=date.today().isoformat()[:7]
    income=c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='收入' AND substr(trans_date,1,7)=?",(month,)).fetchone()[0]
    expense=c.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='支出' AND substr(trans_date,1,7)=?",(month,)).fetchone()[0]
    balance=c.execute('SELECT COALESCE(SUM(balance),0) FROM accounts').fetchone()[0]
    return {'month':month,'income':income,'expense':expense,'balance':balance,'net_cashflow':income-expense,'transaction_count':c.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]}

@app.get('/api/accounts')
def accounts():
    c=db(); return [dict(r) for r in c.execute('SELECT id,name,type,balance FROM accounts ORDER BY balance DESC')]

@app.post('/api/accounts', status_code=201)
def add_account(item: AccountIn):
    c=db(); cur=c.execute('INSERT INTO accounts(name,type,balance) VALUES(?,?,?)',(item.account_name,item.account_type,item.balance)); c.commit(); return {'id':cur.lastrowid,**item.model_dump()}

@app.get('/api/transactions')
def transactions(limit:int=30):
    c=db(); return [dict(r) for r in c.execute('SELECT t.*,a.name account_name FROM transactions t JOIN accounts a ON a.id=t.account_id ORDER BY t.trans_date DESC,t.id DESC LIMIT ?',(limit,))]

@app.post('/api/transactions', status_code=201)
def add_transaction(item: TransactionIn):
    if item.trans_type not in ('收入','支出'): raise HTTPException(400,'交易类型必须是收入或支出')
    c=db(); account=c.execute('SELECT balance FROM accounts WHERE id=?',(item.account_id,)).fetchone()
    if not account: raise HTTPException(404,'账户不存在')
    new_balance=account[0]+(item.amount if item.trans_type=='收入' else -item.amount)
    if new_balance<0: raise HTTPException(400,'账户余额不足')
    cur=c.execute('INSERT INTO transactions(account_id,category,type,amount,remark,trans_date) VALUES(?,?,?,?,?,?)',(item.account_id,item.category,item.trans_type,item.amount,item.remark,item.trans_date)); c.execute('UPDATE accounts SET balance=? WHERE id=?',(new_balance,item.account_id)); c.commit(); return {'id':cur.lastrowid,**item.model_dump()}

@app.get('/api/insights')
def insights():
    data=overview(); items=['当前现金流健康，可以优先建立 3-6 个月的应急金。' if not data['income'] or data['expense']/data['income']<=.7 else '本月支出已超过收入的 70%，建议暂缓非必要消费。',f"可用余额约 ¥{data['balance']:,.0f}，建议为固定支出预留至少一个月的缓冲。"]
    return {'summary':'基于当前账本的轻量分析','items':items}

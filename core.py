"""Course navigation independent of Telegram, with durable per-user state."""
from copy import deepcopy
from html import escape
from pathlib import Path
import json
import sqlite3

ROOT = Path(__file__).resolve().parent
COURSE = json.loads((ROOT / 'data/course.json').read_text(encoding='utf-8'))

class ActionError(ValueError):
    pass

def initial_state():
    return {'screen':'menu', 'module':0, 'page':0, 'visited':{}, 'completed':[],
            'answers':[], 'finished':False, 'rev':0, 'photo_id':None, 'text_id':None}

def transition(state, action):
    s=deepcopy(state)
    if action == 'menu':
        s['screen']='menu'
    elif action.startswith('module:'):
        m=int(action.split(':')[1])
        if not 0 <= m < len(COURSE['modules']):raise ActionError('Раздел не найден.')
        s.update(screen='lesson',module=m,page=0)
        s['visited'].setdefault(str(m),[])
        if 0 not in s['visited'][str(m)]:s['visited'][str(m)].append(0)
    elif action in ('next','back'):
        if s['screen']!='lesson':raise ActionError('Откройте модуль из меню.')
        m=s['module'];last=len(COURSE['modules'][m]['pages'])-1
        if action=='back':s['page']=max(0,s['page']-1)
        elif s['page'] < last:s['page']+=1
        else:
            if len(s['visited'].get(str(m),[])) != last+1:raise ActionError('Прочитайте все страницы модуля.')
            if m not in s['completed']:s['completed'].append(m)
            s['screen']='menu'
        if s['screen']=='lesson':
            visited=s['visited'].setdefault(str(m),[])
            if s['page'] not in visited:visited.append(s['page'])
    elif action in ('quiz','begin'):
        if len(s['completed']) != len(COURSE['modules']):
            raise ActionError('Сначала пройдите все шесть модулей.')
        if s['finished']:s['screen']='result'
        elif action=='quiz':s['screen']='quiz_intro'
        else:s['screen']='question'
    elif action.startswith('answer:'):
        if s['screen']!='question':raise ActionError('Сначала откройте тест.')
        letter=action.split(':')[1]
        if letter not in 'ABCDEF':raise ActionError('Выберите вариант кнопкой.')
        s['answers'].append(letter)
        if len(s['answers'])==len(COURSE['questions']):
            s.update(finished=True,screen='result')
    else:raise ActionError('Откройте меню командой /start.')
    s['rev']+=1
    return s

def view(state):
    """Return image, formatted HTML text, and rows of (label, action)."""
    s=state;screen=s['screen'];rows=[]
    if screen=='menu':
        image='assets/welcome.png';text=escape(COURSE['welcome'])
        for i,m in enumerate(COURSE['modules']):
            prefix='✅' if i in s['completed'] else '📖'
            rows.append([(f'{prefix} {i+1:02} · {m["menu"]}',f'module:{i}')])
        label='📝 Итоговый тест' if len(s['completed'])==6 else '🔒 Итоговый тест'
        rows.append([(label,'quiz')])
    elif screen=='lesson':
        m=COURSE['modules'][s['module']];p=m['pages'][s['page']]
        image=m['image']
        text=(f'<b>🍃 МОДУЛЬ {s["module"]+1:02} · {escape(m["title"])}</b>\n\n'
              f'━━ СТРАНИЦА {s["page"]+1}/{len(m["pages"])} ━━\n\n'
              f'<b>{s["page"]+1:02} · {escape(p["title"])}</b>\n\n'+escape(p['text']))
        nav=[]
        if s['page']:nav.append(('Назад','back'))
        nav.append(('В меню' if s['page']==len(m['pages'])-1 else 'Далее','next'))
        rows.append(nav)
    elif screen=='quiz_intro':
        image='assets/quiz_intro.png';text=escape(COURSE['quiz_intro']);rows=[[('Начать тест','begin')]]
    elif screen=='question':
        q=COURSE['questions'][len(s['answers'])];image=q['image']
        text=f'<b>{escape(q["title"])}</b>\n\n'+escape(q['text'])
        rows=[[(a,'answer:'+a)] for a in 'ABCDEF']
    elif screen=='result':
        image='assets/result.png';text=escape(COURSE['result']);rows=[[('В меню','menu')]]
    else:raise ActionError('Неизвестный экран. Откройте /start.')
    return image,text,rows

class Store:
    def __init__(self,path):
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY,state TEXT NOT NULL)')
        self.db.commit()
    def get(self,uid):
        row=self.db.execute('SELECT state FROM users WHERE id=?',(uid,)).fetchone()
        return json.loads(row[0]) if row else initial_state()
    def put(self,uid,state):
        with self.db:
            self.db.execute('INSERT INTO users VALUES (?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state',
                            (uid,json.dumps(state,ensure_ascii=False)))
    def close(self):self.db.close()

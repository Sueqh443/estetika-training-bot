import unittest
import tempfile
from pathlib import Path
from core import COURSE,ROOT,Store,initial_state,transition,view,ActionError

class CourseTests(unittest.TestCase):
    def trained(self):
        s=initial_state()
        for i in range(6):
            s=transition(s,f'module:{i}')
            for _ in range(6):s=transition(s,'next')
        return s
    def test_quiz_locked_until_all_pages(self):
        with self.assertRaises(ActionError):transition(initial_state(),'quiz')
        s=transition(initial_state(),'module:0')
        for _ in range(5):s=transition(s,'next')
        self.assertFalse(s['completed'])
        s=transition(s,'next')
        self.assertEqual(s['completed'],[0])
        with self.assertRaises(ActionError):transition(s,'begin')
    def test_any_answer_sequence_finishes_same_way(self):
        outputs=set()
        for letter in 'ABCDEF':
            s=transition(self.trained(),'begin')
            for _ in range(10):s=transition(s,'answer:'+letter)
            self.assertTrue(s['finished'])
            self.assertEqual(len(s['answers']),10)
            outputs.add(view(s)[1])
            with self.assertRaises(ActionError):transition(s,'answer:A')
        self.assertEqual(len(outputs),1)
        self.assertIn('Вы справились!',outputs.pop())
    def test_persistence_resume_quiz_and_user_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/'state.sqlite3';a=Store(db)
            s=transition(self.trained(),'begin');s=transition(s,'answer:F');a.put(1,s);a.close()
            b=Store(db);self.assertEqual(b.get(1)['answers'],['F']);self.assertFalse(b.get(2)['completed'])
            s=transition(b.get(1),'menu');s=transition(s,'begin')
            self.assertIn('ВОПРОС 02',view(s)[1]);b.close()
    def test_all_screens_and_assets_fit_telegram_limits(self):
        self.assertEqual(len(COURSE['questions']),10)
        self.assertEqual(sum(len(m['pages']) for m in COURSE['modules']),36)
        def check(s):
            img,text,rows=view(s)
            self.assertTrue((ROOT/img).is_file(),img)
            self.assertLess(len(text.encode('utf-16-le'))//2,4096)
            self.assertNotIn('Территор',text)
            for row in rows:
                for _,a in row:self.assertLessEqual(len((str(s['rev'])+'|'+a).encode()),64)
        s=initial_state();check(s)
        for i in range(6):
            s=transition(s,f'module:{i}')
            for _ in range(6):check(s);s=transition(s,'next')
        s=transition(s,'quiz');check(s);s=transition(s,'begin')
        for _ in range(10):check(s);s=transition(s,'answer:E')
        check(s)

if __name__=='__main__':unittest.main()

class TelegramHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_double_tap_records_only_one_answer_and_old_buttons_are_ignored(self):
        import bot
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from aiogram.types import Message,Chat,User
        from datetime import datetime,timezone
        helper=CourseTests();s=transition(helper.trained(),'begin')
        s.update(photo_id=10,text_id=11)
        with tempfile.TemporaryDirectory() as tmp:
            bot.store=Store(Path(tmp)/'state.sqlite3');bot.store.put(1,s)
            message=Message(message_id=11,date=datetime.now(timezone.utc),chat=Chat(id=1,type='private'))
            query=SimpleNamespace(message=message,from_user=User(id=1,is_bot=False,first_name='Тест'),
                                  data=f'{s["rev"]}|answer:F',answer=AsyncMock())
            fake=SimpleNamespace(edit_message_media=AsyncMock(return_value=True),edit_message_text=AsyncMock(return_value=True))
            await bot.callback(query,fake)
            await bot.callback(query,fake)
            self.assertEqual(bot.store.get(1)['answers'],['F'])
            self.assertEqual(fake.edit_message_text.await_count,1)
            self.assertIn('устарела',query.answer.call_args.args[0])
            bot.store.close()

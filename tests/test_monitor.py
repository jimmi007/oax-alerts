import unittest
from monitor import extract, redacted_structure


class CalendarTests(unittest.TestCase):
    def test_live_list_layout_and_explicit_booking_link(self):
        html = '''<ol><li><ul><li>ΓΗΠΕΔΟ 1</li><li>ΓΗΠΕΔΟ 2</li></ul></li>
        <li><ul><li>20:00<br><a>Νέα Κράτηση</a></li><li>20:00<ul><li>MEMBER NAME</li></ul></li>
        <li>21:00</li><li>21:00<ul><li>LOCK</li></ul></li></ul></li></ol>'''
        slots, detail = extract(html, '2099-01-01', self.config())
        self.assertEqual(slots, [{'date': '2099-01-01', 'court': 'ΓΗΠΕΔΟ 1', 'hour': '20:00'}])
        self.assertEqual(len(detail), 4)
        self.assertNotIn('MEMBER NAME', str(detail))

    def test_structure_redacts_names_credentials_and_links(self):
        html = '<table data-token="secret"><tr><td>ΓΗΠΕΔΟ 1</td><td>20:00</td><td><a href="/?token=secret">MEMBER NAME</a></td></tr></table><input value="password"><script>secret</script><!--secret-->'
        result = redacted_structure(html)
        for private in ('secret', 'MEMBER NAME', 'password', 'href'):
            self.assertNotIn(private, result)
        self.assertIn('20:00', result)
        self.assertIn('ΓΗΠΕΔΟ 1', result)

    def config(self, verified=True):
        return {'hours': ['20:00', '21:00', '22:00'], 'availability_verified': verified,
                'free_text_pattern': r'^Νέα\s+Κράτηση$'}

    def test_free_busy_locked_and_past_are_distinct(self):
        html = '''<table><tr><th>ΓΗΠΕΔΟ 1</th><th>ΓΗΠΕΔΟ 2</th></tr>
        <tr><td>20:00 <a>Νέα Κράτηση</a></td><td>20:00 MEMBER NAME</td></tr>
        <tr><td>21:00 LOCK</td><td>21:00</td></tr></table>'''
        slots, diagnostics = extract(html, '2099-01-01', self.config())
        self.assertEqual(slots, [{'date': '2099-01-01', 'court': 'ΓΗΠΕΔΟ 1', 'hour': '20:00'}])
        self.assertNotIn('MEMBER NAME', str(diagnostics))
        self.assertEqual(extract(html, '2000-01-01', self.config())[0], [])
        self.assertEqual(extract(html, '2099-01-01', self.config(False))[0], [])

    def test_separate_time_column_and_colspan(self):
        html = '''<table><tr><th colspan="3">Ημερολόγιο</th></tr>
        <tr><th>Ώρα</th><th>ΓΗΠΕΔΟ 1</th><th>ΓΗΠΕΔΟ 2</th></tr>
        <tr><th>22:00</th><td>Νέα Κράτηση</td><td>LOCK</td></tr></table>'''
        slots, _ = extract(html, '2099-01-01', self.config())
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]['hour'], '22:00')

    def test_login_and_unrecognized_structure_fail(self):
        for html in ['<form id="login_form"></form>', '<table><tr><td>unknown</td></tr></table>']:
            with self.assertRaises(RuntimeError):
                extract(html, '2099-01-01', self.config())


if __name__ == '__main__':
    unittest.main()

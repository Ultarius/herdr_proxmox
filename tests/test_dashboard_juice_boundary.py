"""Keep backend operations behind shared Juice features in both renderers."""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
DIRECT_TRANSPORT = re.compile(
    r"\b(?:connection|dashboardConnection)\s*\.\s*"
    r"(?:request|requestRead|bytes|text|download)\s*\("
    r"|BlocScope\.get<DashboardBloc>\(\)\s*\.\s*"
    r"(?:request|requestRead|bytes|text|download)\s*\("
    r"|\.execute\(connection\)"
)


class DashboardJuiceBoundaryTests(unittest.TestCase):
    def test_periodic_backend_polling_is_not_owned_by_page_views(self):
        violations = []
        for renderer in ('dashboard', 'jaspr_dashboard'):
            for source in (ROOT / 'web' / renderer / 'lib').rglob('*.dart'):
                # These timers wait for browser layout, not backend evidence.
                if source.name in ('terminal.dart', 'org_chart.dart'):
                    continue
                if re.search(r'\bTimer\s*\.\s*periodic\s*\(',
                             source.read_text(encoding='utf-8')):
                    violations.append(str(source.relative_to(ROOT)))
        self.assertEqual(violations, [],
                         'Own backend polling in a shared feature: '
                         + ', '.join(violations))

    def test_renderers_do_not_bypass_feature_event_ownership(self):
        violations = []
        for renderer in ('dashboard', 'jaspr_dashboard'):
            for source in (ROOT / 'web' / renderer / 'lib').rglob('*.dart'):
                contents = source.read_text(encoding='utf-8')
                for match in DIRECT_TRANSPORT.finditer(contents):
                    line = contents.count('\n', 0, match.start()) + 1
                    violations.append(f'{source.relative_to(ROOT)}:{line}')
        self.assertEqual(
            violations, [],
            'Move backend operations into a shared Juice feature before wiring '
            'them into a renderer: ' + ', '.join(violations),
        )


if __name__ == '__main__':
    unittest.main()

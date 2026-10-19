from pathlib import Path
import re
import unittest


class HomepageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

    def test_no_working_papers_in_public_source(self):
        self.assertNotRegex(self.html.lower(), r"working[\s-]+papers?")
        self.assertNotIn("Causal-OPD", self.html)
        self.assertNotIn("VUG-RAG", self.html)

    def test_published_paper_and_preprint_are_retained(self):
        self.assertIn("Unspoken Details:", self.html)
        self.assertIn("<h3>Preprints</h3>", self.html)
        self.assertIn("Emission-GPT:", self.html)

    def test_navigation_has_no_missing_targets(self):
        targets = set(re.findall(r'\bid="([^"]+)"', self.html))
        links = re.findall(r'href="#([^"]+)"', self.html)
        self.assertTrue(links)
        self.assertTrue(set(links).issubset(targets))


if __name__ == "__main__":
    unittest.main()

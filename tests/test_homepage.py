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

    def test_news_section_and_navigation_are_removed(self):
        self.assertNotIn('id="news"', self.html)
        self.assertNotIn('href="#news"', self.html)
        self.assertNotIn("<h2>News</h2>", self.html)

    def test_published_paper_and_preprint_are_retained(self):
        self.assertIn("Unspoken Details:", self.html)
        self.assertIn("<h3>Preprints</h3>", self.html)
        self.assertIn("Emission-GPT:", self.html)

    def test_navigation_has_no_missing_targets(self):
        targets = set(re.findall(r'\bid="([^"]+)"', self.html))
        links = re.findall(r'href="#([^"]+)"', self.html)
        self.assertTrue(links)
        self.assertTrue(set(links).issubset(targets))

    def test_private_projects_are_not_linked_from_the_homepage(self):
        for name in ("AgenticVideoRAG-public", "factor_mining-rag",
                     "PublicAdmin-Supervisor-Skills-CN", "academic-beamer-loop"):
            self.assertNotIn(f"https://github.com/Lam810/{name}", self.html)

    def test_retained_ascend_projects_are_still_present(self):
        for name in ("cantonese-llm-lab", "NexusFlow"):
            self.assertIn(f"https://github.com/Lam810/{name}", self.html)


if __name__ == "__main__":
    unittest.main()

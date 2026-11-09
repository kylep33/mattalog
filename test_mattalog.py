import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

import mattalog

HERE = os.path.dirname(os.path.abspath(__file__))
ANNAS = "Dune_ Book One -- Herbert, Frank -- Ace, 1990 -- 9780441172719 -- abc123 -- Anna's Archive.pdf"


def make_epub(path, title, creator):
    meta = f"<dc:title>{title}</dc:title><dc:creator>{creator}</dc:creator>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="c.opf"/></rootfiles></container>')
        z.writestr("c.opf", f'<package><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">{meta}</metadata></package>')


class Autoname(unittest.TestCase):
    def test_annas_filename(self):
        self.assertEqual(mattalog.autoname("/x/" + ANNAS), "Dune - Frank Herbert.pdf")

    def test_unknown_name_is_untouched(self):
        self.assertEqual(mattalog.autoname("/x/notes.pdf"), "notes.pdf")

    def test_epub_metadata_beats_filename(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "long -- junk -- name.epub")
            make_epub(p, "Foundation: The First Novel", "Isaac Asimov")
            self.assertEqual(mattalog.autoname(p), "Foundation - Isaac Asimov.epub")

    def test_metadata_cannot_escape_folder(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.epub")
            make_epub(p, "../../etc/passwd", "x/y")
            self.assertNotIn("/", mattalog.autoname(p))

    def test_corrupt_epub_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "broken.epub")
            with open(p, "w") as f:
                f.write("not a zip")
            self.assertEqual(mattalog.autoname(p), "broken.epub")

    def test_long_names_fit_filesystem_limit(self):
        self.assertLessEqual(len(mattalog.safe_filename("é" * 400, ".epub").encode()), 255)


class SplitTarget(unittest.TestCase):
    def test_remote(self):
        self.assertEqual(mattalog.split_target("kile@mattbot:/srv/x/"), ("kile", "mattbot", "/srv/x"))

    def test_tilde_becomes_home_relative(self):
        self.assertEqual(mattalog.split_target("kile@mattbot:~/dev"), ("kile", "mattbot", "dev"))
        self.assertEqual(mattalog.split_target("kile@mattbot:~"), ("kile", "mattbot", "."))

    def test_local(self):
        self.assertEqual(mattalog.split_target("/a/b:c"), (None, None, "/a/b:c"))


class EndToEnd(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = tmp.name
        self.out = os.path.join(self.d, "out")
        os.makedirs(os.path.join(self.d, "mattalog"))
        with open(os.path.join(self.d, "mattalog", "config.json"), "w") as f:
            json.dump({"destinations": {"books": self.out}, "servers": {}}, f)

    def run_cli(self, *args):
        env = dict(os.environ, XDG_CONFIG_HOME=self.d)
        return subprocess.run([sys.executable, os.path.join(HERE, "mattalog.py"), *args], env=env, capture_output=True, text=True, cwd=self.d)

    def write(self, name, text="hello"):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_copies_several_and_keeps_originals(self):
        a, b = self.write("a.txt"), self.write("b.txt")
        self.assertEqual(self.run_cli("books", a, b).returncode, 0)
        self.assertEqual(sorted(os.listdir(self.out)), ["a.txt", "b.txt"])
        self.assertTrue(os.path.exists(a))

    def test_directory_with_trailing_slash_stays_a_directory(self):
        os.makedirs(os.path.join(self.d, "series"))
        self.write("series/one.txt")
        self.assertEqual(self.run_cli("books", os.path.join(self.d, "series") + "/").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.out, "series", "one.txt")))

    def test_autoname_flag_after_positionals(self):
        p = self.write(ANNAS)
        r = self.run_cli("books", p, "--autoname")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.out), ["Dune - Frank Herbert.pdf"])

    def test_never_overwrites_and_says_so(self):
        p = self.write("a.txt", "new")
        os.makedirs(self.out)
        with open(os.path.join(self.out, "a.txt"), "w") as f:
            f.write("old")
        r = self.run_cli("books", p)
        with open(os.path.join(self.out, "a.txt")) as f:
            self.assertEqual(f.read(), "old")
        self.assertIn("exists", r.stdout)

    def test_missing_source_copies_nothing(self):
        p = self.write("a.txt")
        self.assertNotEqual(self.run_cli("books", p, "/nope").returncode, 0)
        self.assertFalse(os.path.exists(self.out))

    def test_singular_alias_and_unknown(self):
        self.assertEqual(self.run_cli("book", self.write("a.txt")).returncode, 0)
        self.assertIn("unknown destination", self.run_cli("zzz", self.write("b.txt")).stderr)


if __name__ == "__main__":
    unittest.main()

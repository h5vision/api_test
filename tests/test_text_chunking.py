from __future__ import annotations

import unittest

from backend.text import chunk_text_with_metadata, classify_index_path, is_low_information_chunk


class StructuredChunkingTests(unittest.TestCase):
    def test_python_functions_use_structural_boundaries(self) -> None:
        content = """module_value = 1

def first_function():
    value = 'first' * 20
    return value

def second_function():
    value = 'second' * 20
    return value
"""
        chunks = chunk_text_with_metadata(
            content, 110, 20, path="backend/example.py", language="python"
        )

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(item["chunking_strategy"] == "structural-boundary-v1" for item in chunks))
        self.assertTrue(all(item["path_category"] == "code" for item in chunks))

    def test_translation_and_release_note_classification(self) -> None:
        translated = classify_index_path("docs/ko/docs/tutorial/security.md", "markdown")
        release = classify_index_path("docs/en/docs/release-notes.md", "markdown")

        self.assertEqual(translated["locale"], "ko")
        self.assertTrue(translated["is_translation"])
        self.assertEqual(release["path_category"], "release_notes")

    def test_github_discussion_template_is_not_treated_as_runtime_config(self) -> None:
        profile = classify_index_path(
            ".github/DISCUSSION_TEMPLATE/questions.yml", "yaml"
        )

        self.assertEqual(profile["path_category"], "community")
        self.assertEqual(profile["content_type"], "documentation")

    def test_syntax_debris_is_low_information(self) -> None:
        self.assertTrue(is_low_information_chunk("''' } });"))
        self.assertFalse(is_low_information_chunk("def validate_token(token): return verify(token)"))


if __name__ == "__main__":
    unittest.main()

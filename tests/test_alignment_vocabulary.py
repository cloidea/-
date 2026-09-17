import importlib.util
import unittest

from subtitles.align_worker import _acoustic_candidates, _alignment_units, _extend_emission


class Vocabulary:
    unk_token_id = 0
    all_special_ids = [0]
    vocab = {"<unk>": 0, "是": 1, "世": 2, "诗": 3, "苏": 4}

    def get_vocab(self):
        return self.vocab

    def convert_tokens_to_ids(self, char):
        return self.vocab.get(char, 0)


class VocabularyTests(unittest.TestCase):
    def test_known_characters_unchanged(self):
        self.assertEqual(_acoustic_candidates(list("苏是"), Vocabulary()), [[4], [1]])

    @unittest.skipUnless(importlib.util.find_spec("pypinyin"), "run in alignment runtime")
    def test_rare_character_uses_matching_tone_and_preserves_original(self):
        units = _alignment_units("苏轼")
        self.assertEqual(units, [("苏", "苏"), ("轼", "轼")])
        self.assertEqual(_acoustic_candidates(list("苏轼"), Vocabulary()), [[4], [1, 2]])

    @unittest.skipUnless(importlib.util.find_spec("pypinyin"), "run in alignment runtime")
    def test_unsupported_symbol_is_not_given_arbitrary_phonetics(self):
        with self.assertRaises(RuntimeError):
            _acoustic_candidates(["😀"], Vocabulary())

    @unittest.skipUnless(importlib.util.find_spec("torch"), "run in alignment runtime")
    def test_virtual_scores_leave_original_columns_unchanged(self):
        import torch
        source = torch.tensor([[0.1, 0.2, 0.3, 0.4]]).log()
        extended, ids = _extend_emission(source, [[1], [1, 2], [1, 2]])
        self.assertEqual(ids, [1, 4, 4])
        self.assertTrue(torch.equal(extended[:, :4], source))
        self.assertAlmostEqual(extended[0, 4].exp().item(), 0.5, places=5)

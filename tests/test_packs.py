"""Domain packs. No network."""

from __future__ import annotations

import pytest
import yaml

from halligan.cli import EXIT_CONFIG, EXIT_OK, _asset_root, main
from halligan.config import load_suites
from halligan.packs import PROVENANCE, Pack, available_packs, load_pack, resolve_suites

MINIMAL = {"name": "p", "suites": ["suites/a.yaml"]}


class TestPackParsing:
    def test_name_is_required(self):
        with pytest.raises(ValueError, match="name"):
            Pack.from_yaml({"suites": ["a.yaml"]})

    def test_a_pack_with_no_suites_is_rejected(self):
        """An empty pack would silently run nothing and report a clean pass."""
        with pytest.raises(ValueError, match="no suites"):
            Pack.from_yaml({"name": "p"})

    def test_defaults_to_the_weaker_provenance_claim(self):
        """Unstated provenance must not read as 'battle-tested'."""
        pack = Pack.from_yaml(MINIMAL)
        assert pack.provenance == "authored"
        assert not pack.reviewed

    def test_every_provenance_value_has_a_label(self):
        for value in PROVENANCE:
            assert Pack.from_yaml({**MINIMAL, "provenance": value}).provenance_label

    def test_unknown_provenance_falls_back_to_the_raw_value(self):
        pack = Pack.from_yaml({**MINIMAL, "provenance": "mystery"})
        assert pack.provenance_label == "mystery"
        assert not pack.reviewed


class TestBundledCatholicPack:
    def test_it_ships(self):
        root = _asset_root()
        assert root is not None
        names = [p.name for p in available_packs(root)]
        assert "catholic" in names

    def test_it_resolves_to_the_real_suites(self):
        root = _asset_root()
        pack = load_pack(root, "catholic")
        paths = resolve_suites(root, pack)
        assert len(paths) == 9
        assert sum(len(s.cases) for s in load_suites(paths)) == 74

    def test_it_ships_the_policy_it_assumes(self):
        """A pack without its policy is half a pack — the probes assume it."""
        root = _asset_root()
        pack = load_pack(root, "catholic")
        assert pack.policy
        assert (root / pack.policy).is_file()

    def test_it_claims_real_provenance(self):
        root = _asset_root()
        pack = load_pack(root, "catholic")
        assert pack.reviewed
        assert pack.provenance_note

    def test_unknown_pack_names_the_ones_that_exist(self):
        root = _asset_root()
        with pytest.raises(ValueError, match="catholic"):
            load_pack(root, "nope")


class TestPackRobustness:
    def test_a_broken_pack_does_not_break_the_listing(self, tmp_path):
        (tmp_path / "packs").mkdir()
        (tmp_path / "packs" / "good.yaml").write_text(
            yaml.safe_dump({"name": "good", "suites": ["s.yaml"]}), encoding="utf-8"
        )
        (tmp_path / "packs" / "broken.yaml").write_text("{[not yaml", encoding="utf-8")
        (tmp_path / "packs" / "empty.yaml").write_text(
            yaml.safe_dump({"name": "empty"}), encoding="utf-8"
        )
        assert [p.name for p in available_packs(tmp_path)] == ["good"]

    def test_no_packs_directory_is_not_an_error(self, tmp_path):
        assert available_packs(tmp_path) == []

    def test_missing_suite_files_are_reported_by_name(self, tmp_path):
        pack = Pack.from_yaml({"name": "p", "suites": ["suites/gone.yaml"]})
        with pytest.raises(ValueError, match="gone.yaml"):
            resolve_suites(tmp_path, pack)


class TestPackCli:
    def test_packs_lists_the_catholic_pack(self, capsys):
        assert main(["packs"]) == EXIT_OK
        assert "catholic" in capsys.readouterr().out

    def test_run_accepts_a_pack_instead_of_suites(self, tmp_path, capsys):
        target = tmp_path / "t.yaml"
        target.write_text(
            "name: t\nprovider:\n  name: ollama\n  model: m\n"
            "judge:\n  name: ollama\n  model: m\n",
            encoding="utf-8",
        )
        assert main(["run", "-t", str(target), "--pack", "catholic", "--estimate"]) == EXIT_OK
        assert "74 case run(s)" in capsys.readouterr().out

    def test_unknown_pack_is_a_config_error(self, tmp_path, capsys):
        target = tmp_path / "t.yaml"
        target.write_text("name: t\nprovider:\n  name: ollama\n  model: m\n", encoding="utf-8")
        assert main(["run", "-t", str(target), "--pack", "nope"]) == EXIT_CONFIG
        assert "nope" in capsys.readouterr().err

    def test_neither_suite_nor_pack_is_a_config_error(self, tmp_path, capsys):
        """--suite stopped being required when --pack arrived; don't run nothing."""
        target = tmp_path / "t.yaml"
        target.write_text("name: t\nprovider:\n  name: ollama\n  model: m\n", encoding="utf-8")
        assert main(["run", "-t", str(target)]) == EXIT_CONFIG
        assert "nothing to run" in capsys.readouterr().err

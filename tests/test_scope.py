

# ---- audit-log minimisation (EA-20) ---------------------------------------- #

def _scope(tmp_path, **extra):
    from attribution_graph import CaseScope

    lines = ["case_ref: T", "authorization: t", "seeds: [domain:a.example]",
             f"audit_path: {tmp_path / 'a.jsonl'}"]
    lines += [f"{k}: {v}" for k, v in extra.items()]
    (tmp_path / "c.yaml").write_text("\n".join(lines) + "\n")
    return CaseScope.load(str(tmp_path / "c.yaml"))


def _last_audit(tmp_path):
    import json
    return json.loads((tmp_path / "a.jsonl").read_text().strip().splitlines()[-1])


def test_sensitive_audit_fields_are_minimised_at_the_serializer(tmp_path):
    """Minimisation used to depend on caller discipline: audit() serialised
    whatever it was handed, so any collector could write a plain email into the
    JSONL while graph exports were salted. A privacy invariant enforced by
    convention is not enforced.
    """
    import json

    s = _scope(tmp_path, minimize="true",
               salt="00112233445566778899aabbccddeeff")
    s.audit("lookup", email="person@example.com", handle="kr4ken")
    line = _last_audit(tmp_path)

    assert line["email"].startswith("min:")
    assert line["handle"].startswith("min:")
    assert "person@example.com" not in json.dumps(line)
    assert "kr4ken" not in json.dumps(line)


def test_non_sensitive_audit_fields_are_preserved(tmp_path):
    """Minimisation must not blind the audit log to what it is for."""
    s = _scope(tmp_path, minimize="true",
               salt="00112233445566778899aabbccddeeff")
    s.audit("lookup", collector="gleif", count=3, status=200)
    line = _last_audit(tmp_path)
    assert line["collector"] == "gleif"
    assert line["count"] == 3
    assert line["status"] == 200


def test_minimisation_is_on_by_default(tmp_path):
    """The safe posture is the default one: a case file that says nothing about
    minimisation still gets it."""
    s = _scope(tmp_path)
    assert s.minimize is True
    s.audit("lookup", email="person@example.com")
    assert _last_audit(tmp_path)["email"].startswith("min:")


def test_minimisation_can_be_turned_off_explicitly(tmp_path):
    s = _scope(tmp_path, minimize="false")
    s.audit("lookup", email="person@example.com")
    assert _last_audit(tmp_path)["email"] == "person@example.com"


def test_evidence_operator_and_host_are_opt_in():
    """Evidence packages are designed to be shared; silently embedding the
    analyst's account name and workstation hostname is the opposite of the
    minimisation applied everywhere else."""
    from attribution_graph import evidence as ev

    assert ev._disclosed_operator() == "(not disclosed)"
    assert ev._disclosed_hostname() == "(not disclosed)"

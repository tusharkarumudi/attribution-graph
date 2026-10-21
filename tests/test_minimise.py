

# ---- masking must not eat ordinary words ----------------------------------- #

def test_a_value_is_replaced_only_at_token_boundaries():
    """A plain substring replace made a company suffix eat every word
    containing it: "Inc" turned "INCOMPLETE RESULT" into "min:7a31…OMPLETE
    RESULT" and "incorporated_in" into "min:7a31…orporated_in". The whole
    report was corrupted wherever a value was a common substring."""
    from attribution_graph.minimise import scrub

    text = ("> ## :: INCOMPLETE RESULT ::  incorporated_in: GB  "
            "Example Adtech Inc trades here")
    out = scrub(text, {"Inc"}, b"salt")
    assert "INCOMPLETE RESULT" in out
    assert "incorporated_in" in out
    assert "Example Adtech min:" in out, "the value must still be masked"


def test_domains_and_punctuated_values_still_mask():
    from attribution_graph.minimise import scrub

    out = scrub("see other-site.example/about and notother-site.community",
                {"other-site.example"}, b"salt")
    assert "min:" in out
    assert "notother-site.community" in out, "a longer word must not be broken"

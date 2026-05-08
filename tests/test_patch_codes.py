from eosp.services.patch_codes import iata_from_destination


def test_iata_from_destination_strips_country_prefix():
    assert iata_from_destination("ZA_JNB") == "JNB"
    assert iata_from_destination("AMS") == "AMS"

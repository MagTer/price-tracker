"""Tests for domain/deals.py — THE deal verdict and margin.

The query itself is exercised through its consumers (the /deals route in test_api.py and
service.get_current_deals in test_service.py, both over mocked sessions); these tests pin
the pure judgement, which the portal's classifyDeal/dealSavings now merely READ.
"""

import pytest

from domain.deals import (
    DEAL_BEST,
    DEAL_UNKNOWN,
    DEAL_WORSE,
    classify_deal,
    deal_savings,
)


class TestClassifyDeal:
    def test_cheaper_than_alternative_is_best(self) -> None:
        assert classify_deal(5.00, 6.24) == DEAL_BEST

    def test_equal_price_is_best(self) -> None:
        """'Lika billigt' is still billigast — a tie must not read as a warning."""
        assert classify_deal(5.00, 5.00) == DEAL_BEST

    def test_pricier_than_alternative_is_worse(self) -> None:
        assert classify_deal(6.24, 5.00) == DEAL_WORSE

    def test_no_own_unit_price_is_unknown(self) -> None:
        """No amount on the link -> no honest comparison, never a guess."""
        assert classify_deal(None, 5.00) == DEAL_UNKNOWN

    def test_no_alternative_is_unknown(self) -> None:
        """The product's only link: nothing to compare against."""
        assert classify_deal(5.00, None) == DEAL_UNKNOWN


class TestDealSavings:
    def test_positive_margin_when_the_offer_wins(self) -> None:
        assert deal_savings(5.00, 6.24) == pytest.approx(1.24)

    def test_negative_margin_when_the_offer_loses(self) -> None:
        assert deal_savings(6.24, 5.00) == pytest.approx(-1.24)

    def test_none_when_no_comparison_exists(self) -> None:
        """Never 0.0 — that would claim a tie we cannot see."""
        assert deal_savings(None, 5.00) is None
        assert deal_savings(5.00, None) is None

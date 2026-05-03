import unittest
import uuid

from agents.offer_agent import OfferAgent
from models.shared_state import SharedState, SessionMeta, RiskBand


class OfferPolicyTests(unittest.TestCase):
    def _state(self) -> SharedState:
        return SharedState(session_meta=SessionMeta(call_id=str(uuid.uuid4()), session_token="t"))

    def test_missing_verified_income_is_pending_docs(self):
        state = self._state()
        state.financial_data.monthly_income = 90000
        state.financial_data.verified_income = None
        state.financial_data.risk_band = RiskBand.LOW
        state.financial_data.bureau_score = 760

        result = OfferAgent()._run_policy_engine(state)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "ELIGIBILITY_PENDING_DOCS")


if __name__ == "__main__":
    unittest.main()

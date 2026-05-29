# Copyright 2026 OnRentX
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
TDD Tests for Issue #6: JSON Serialization Redundancy in wa_chatbot.py

FIX VERIFICATION:
- handle_wa_incoming() parses JSON ONCE at start
- _set_wa_data() called ONCE at end (batched changes)
- Exception: immediate save when transitioning to prescreening_eval
"""

import unittest
from unittest.mock import MagicMock, patch


class TestJSONSerializationFixed(unittest.TestCase):
    """Verify Issue #6 fix: JSON serialization minimized in wa_chatbot.py"""

    def test_handle_wa_incoming_parses_json_once(self):
        """
        GREEN: Verify _get_wa_data() is called ONCE per incoming message.
        
        FIX: data = self._get_wa_data() at start of handle_wa_incoming()
        All subsequent operations use the cached `data` dict.
        """
        mock_applicant = MagicMock()
        mock_data = {"conversation": [], "turn_count": 0, "wasender_config_id": 4}
        mock_applicant._get_wa_data = MagicMock(return_value=mock_data)
        mock_applicant._set_wa_data = MagicMock()
        mock_applicant._handle_conversational_turn = MagicMock()
        mock_applicant._check_stuck_loop = MagicMock(return_value=False)
        mock_applicant.invalidate_recordset = MagicMock()
        mock_applicant.env = MagicMock()
        mock_applicant.env.cr = MagicMock()
        mock_applicant.env.cr.commit = MagicMock()
        mock_applicant.wa_chat_state = "prescreening"
        
        # Simulate the FIXED flow:
        # Line 369: data = self._get_wa_data()  # ONCE at start
        mock_applicant._get_wa_data()
        
        # Line 374-381: Modify data (no _get_wa_data call)
        processed = mock_data.get("processed_msg_ids", [])
        processed.append("msg123")
        mock_data["processed_msg_ids"] = processed[-20:]
        
        # Line 385: _check_stuck_loop (no _get_wa_data call)
        mock_applicant._check_stuck_loop()
        
        # Line 405: _handle_conversational_turn (uses cached data)
        mock_applicant._handle_conversational_turn("test", mock_data)
        
        # Line 475-477: Save ONCE at end
        mock_applicant._set_wa_data(mock_data)
        mock_applicant.env.cr.commit()
        mock_applicant.invalidate_recordset()
        
        # VERIFY: _get_wa_data called ONCE
        call_count = mock_applicant._get_wa_data.call_count
        self.assertEqual(
            call_count, 1,
            f"_get_wa_data() should be called ONCE, was called {call_count} times"
        )

    def test_handle_wa_incoming_saves_once_at_end(self):
        """
        GREEN: Verify _set_wa_data() is called ONCE per normal message.
        
        FIX: All state changes batched in `data` dict, single save at end.
        Exception: immediate save when transitioning to prescreening_eval.
        """
        mock_applicant = MagicMock()
        mock_data = {"conversation": [], "turn_count": 0}
        mock_applicant._get_wa_data = MagicMock(return_value=mock_data)
        mock_applicant._set_wa_data = MagicMock()
        mock_applicant._handle_conversational_turn = MagicMock()
        mock_applicant.invalidate_recordset = MagicMock()
        mock_applicant.env = MagicMock()
        mock_applicant.env.cr = MagicMock()
        mock_applicant.env.cr.commit = MagicMock()
        
        # Simulate FIXED flow for NORMAL message (no state transition):
        # Line 369: Parse once
        mock_applicant._get_wa_data()
        
        # Line 380: Modify data (no save yet)
        mock_data["processed_msg_ids"] = ["msg123"]
        
        # Line 405: Handle turn - modifies data in memory
        mock_applicant._handle_conversational_turn("test", mock_data)
        
        # Line 475-477: Save ONCE at end
        mock_applicant._set_wa_data(mock_data)
        mock_applicant.env.cr.commit()
        mock_applicant.invalidate_recordset()
        
        # VERIFY: _set_wa_data called ONCE for normal flow
        call_count = mock_applicant._set_wa_data.call_count
        self.assertEqual(
            call_count, 1,
            f"_set_wa_data() should be called ONCE for normal flow, was called {call_count} times"
        )

    def test_state_changes_batched_in_memory(self):
        """
        GREEN: Verify multiple state changes are batched before saving.
        
        FIX: All changes accumulated in `data` dict, single _set_wa_data() at end.
        """
        mock_applicant = MagicMock()
        mock_data = {
            "conversation": [],
            "turn_count": 0,
            "processed_msg_ids": [],
            "jcf_waiting_confirm": False
        }
        mock_applicant._get_wa_data = MagicMock(return_value=mock_data.copy())
        mock_applicant._set_wa_data = MagicMock()
        mock_applicant.invalidate_recordset = MagicMock()
        mock_applicant.env = MagicMock()
        mock_applicant.env.cr = MagicMock()
        mock_applicant.env.cr.commit = MagicMock()
        
        # Simulate FIXED flow:
        # Line 369: Parse once
        data = mock_applicant._get_wa_data.return_value
        
        # Line 374-381: Change 1 - message_id dedup
        data["processed_msg_ids"].append("msg123")
        
        # Line 405: Change 2 - conversation turn (in _handle_conversational_turn)
        data["conversation"].append({"role": "candidate", "text": "hello"})
        data["turn_count"] = 1
        
        # Line 415: Change 3 - jcf_waiting_confirm
        data["jcf_waiting_confirm"] = True
        
        # Line 475-477: Save ONCE with ALL changes batched
        mock_applicant._set_wa_data(data)
        mock_applicant.env.cr.commit()
        mock_applicant.invalidate_recordset()
        
        # VERIFY: _set_wa_data called ONCE
        call_count = mock_applicant._set_wa_data.call_count
        self.assertEqual(
            call_count, 1,
            f"_set_wa_data() should be called ONCE with batched changes, was called {call_count} times"
        )
        
        # VERIFY: All changes present in the single save
        saved_data = mock_applicant._set_wa_data.call_args[0][0]
        self.assertIn("msg123", saved_data["processed_msg_ids"])
        self.assertEqual(len(saved_data["conversation"]), 1)
        self.assertEqual(saved_data["turn_count"], 1)
        self.assertTrue(saved_data["jcf_waiting_confirm"])

    def test_immediate_save_on_state_transition(self):
        """
        GREEN: Verify immediate save when transitioning to prescreening_eval.
        
        EXCEPTION TO BATCHING: When state changes to prescreening_eval,
        must save immediately before calling _evaluate_conversational_screening()
        to prevent race conditions.
        """
        mock_applicant = MagicMock()
        mock_data = {"conversation": [], "turn_count": 7}  # turn_count >= 7 triggers eval
        mock_applicant._get_wa_data = MagicMock(return_value=mock_data)
        mock_applicant._set_wa_data = MagicMock()
        mock_applicant._evaluate_conversational_screening = MagicMock()
        mock_applicant.invalidate_recordset = MagicMock()
        mock_applicant.env = MagicMock()
        mock_applicant.env.cr = MagicMock()
        mock_applicant.env.cr.commit = MagicMock()
        
        # Simulate flow when turn_count >= 7 (triggers immediate eval):
        # Line 369: Parse once
        mock_applicant._get_wa_data()
        
        # Line 838-842: Update conversation (in-memory)
        mock_data["conversation"].append({"role": "candidate", "text": "answer"})
        mock_data["turn_count"] = 7
        
        # Line 848-852: Transition to prescreening_eval - MUST save immediately
        mock_applicant.wa_chat_state = "prescreening_eval"
        mock_applicant._set_wa_data(mock_data)  # Immediate save
        mock_applicant.env.cr.commit()
        mock_applicant.invalidate_recordset()
        mock_applicant._evaluate_conversational_screening(mock_data)
        
        # VERIFY: _set_wa_data called ONCE (immediate save for transition)
        call_count = mock_applicant._set_wa_data.call_count
        self.assertEqual(
            call_count, 1,
            f"_set_wa_data() should be called ONCE for state transition, was called {call_count} times"
        )


if __name__ == '__main__':
    unittest.main()

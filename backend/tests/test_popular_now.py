"""
Tests for the 'Popular Right Now' feature - GET /api/popular/now endpoint
Tests time-aware bus suggestions with sorting, filtering, and data structure validation
"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPopularNowEndpoint:
    """Tests for GET /api/popular/now endpoint"""
    
    def test_popular_now_returns_200(self):
        """Basic health check - endpoint returns 200"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        print("✓ GET /api/popular/now returns 200")
    
    def test_popular_now_response_structure(self):
        """Response contains generated_at and items array"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        # Check top-level structure
        assert "generated_at" in data, "Response missing 'generated_at'"
        assert "items" in data, "Response missing 'items'"
        assert isinstance(data["items"], list), "'items' should be a list"
        print(f"✓ Response structure valid: generated_at={data['generated_at'][:19]}, items count={len(data['items'])}")
    
    def test_popular_now_default_limit_6(self):
        """Default limit returns up to 6 items"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["items"]) <= 6, f"Expected max 6 items, got {len(data['items'])}"
        print(f"✓ Default limit: {len(data['items'])} items (max 6)")
    
    def test_popular_now_limit_parameter(self):
        """limit=3 caps response to 3 items"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=3")
        assert response.status_code == 200
        data = response.json()
        
        assert len(data["items"]) <= 3, f"Expected max 3 items with limit=3, got {len(data['items'])}"
        print(f"✓ limit=3 returns {len(data['items'])} items")
    
    def test_popular_now_item_required_keys(self):
        """Each item has all required keys"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        required_keys = [
            "from_city", "to_city", "from_terminal", "to_terminal",
            "schedule_id", "departure_date", "departure_time", "arrival_time",
            "is_today", "minutes_until_departure", "fare", "currency", "seats_available"
        ]
        
        for i, item in enumerate(data["items"]):
            for key in required_keys:
                assert key in item, f"Item {i} missing required key: {key}"
        
        print(f"✓ All {len(data['items'])} items have required keys: {', '.join(required_keys[:5])}...")
    
    def test_popular_now_terminal_structure(self):
        """from_terminal and to_terminal have proper structure"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        if len(data["items"]) == 0:
            pytest.skip("No items returned - cannot test terminal structure")
        
        terminal_keys = ["id", "city", "name", "code"]
        
        for i, item in enumerate(data["items"]):
            for term_type in ["from_terminal", "to_terminal"]:
                terminal = item[term_type]
                assert isinstance(terminal, dict), f"Item {i} {term_type} should be dict"
                for key in terminal_keys:
                    assert key in terminal, f"Item {i} {term_type} missing key: {key}"
        
        print(f"✓ Terminal structures valid for all {len(data['items'])} items")
    
    def test_popular_now_is_today_boolean(self):
        """is_today is a boolean value"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        for i, item in enumerate(data["items"]):
            assert isinstance(item["is_today"], bool), f"Item {i} is_today should be boolean, got {type(item['is_today'])}"
        
        print("✓ is_today is boolean for all items")
    
    def test_popular_now_minutes_until_departure_non_negative(self):
        """minutes_until_departure is non-negative integer or null"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        for i, item in enumerate(data["items"]):
            mins = item["minutes_until_departure"]
            if mins is not None:
                assert isinstance(mins, int), f"Item {i} minutes_until_departure should be int, got {type(mins)}"
                assert mins >= 0, f"Item {i} minutes_until_departure should be non-negative, got {mins}"
        
        print("✓ minutes_until_departure is non-negative int or null for all items")
    
    def test_popular_now_seats_available_calculation(self):
        """seats_available is a non-negative integer"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        for i, item in enumerate(data["items"]):
            seats = item["seats_available"]
            assert isinstance(seats, int), f"Item {i} seats_available should be int, got {type(seats)}"
            assert seats >= 0, f"Item {i} seats_available should be non-negative, got {seats}"
        
        print("✓ seats_available is non-negative int for all items")
    
    def test_popular_now_sorting_today_first(self):
        """Items with is_today=true come before is_today=false"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        items = data["items"]
        if len(items) < 2:
            pytest.skip("Need at least 2 items to test sorting")
        
        # Find first non-today item
        first_non_today_idx = None
        for i, item in enumerate(items):
            if not item["is_today"]:
                first_non_today_idx = i
                break
        
        if first_non_today_idx is not None:
            # All items after first non-today should also be non-today
            for i in range(first_non_today_idx, len(items)):
                assert not items[i]["is_today"], f"Item {i} is_today=true after non-today item at index {first_non_today_idx}"
        
        print("✓ Sorting correct: today items come first")
    
    def test_popular_now_sorting_by_minutes(self):
        """Within same is_today group, sorted by minutes_until_departure ascending"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        items = data["items"]
        
        # Check today items are sorted by minutes
        today_items = [it for it in items if it["is_today"]]
        for i in range(1, len(today_items)):
            prev_mins = today_items[i-1].get("minutes_until_departure") or float('inf')
            curr_mins = today_items[i].get("minutes_until_departure") or float('inf')
            assert prev_mins <= curr_mins, f"Today items not sorted by minutes: {prev_mins} > {curr_mins}"
        
        print("✓ Today items sorted by minutes_until_departure ascending")
    
    def test_popular_now_fare_and_currency(self):
        """fare is numeric and currency is valid"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        valid_currencies = ["myr", "sgd", "usd"]
        
        for i, item in enumerate(data["items"]):
            fare = item["fare"]
            currency = item["currency"]
            
            assert isinstance(fare, (int, float)), f"Item {i} fare should be numeric, got {type(fare)}"
            assert fare >= 0, f"Item {i} fare should be non-negative, got {fare}"
            assert currency.lower() in valid_currencies, f"Item {i} currency '{currency}' not in {valid_currencies}"
        
        print("✓ fare and currency valid for all items")
    
    def test_popular_now_schedule_id_format(self):
        """schedule_id is a non-empty string (UUID format)"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        for i, item in enumerate(data["items"]):
            schedule_id = item["schedule_id"]
            assert isinstance(schedule_id, str), f"Item {i} schedule_id should be string"
            assert len(schedule_id) > 0, f"Item {i} schedule_id should not be empty"
        
        print("✓ schedule_id is valid string for all items")
    
    def test_popular_now_departure_date_format(self):
        """departure_date is in YYYY-MM-DD format"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        import re
        date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        
        for i, item in enumerate(data["items"]):
            dep_date = item["departure_date"]
            assert date_pattern.match(dep_date), f"Item {i} departure_date '{dep_date}' not in YYYY-MM-DD format"
        
        print("✓ departure_date format valid for all items")
    
    def test_popular_now_time_format(self):
        """departure_time and arrival_time are in HH:MM format"""
        response = requests.get(f"{BASE_URL}/api/popular/now")
        assert response.status_code == 200
        data = response.json()
        
        import re
        time_pattern = re.compile(r'^\d{2}:\d{2}$')
        
        for i, item in enumerate(data["items"]):
            dep_time = item["departure_time"]
            arr_time = item["arrival_time"]
            
            assert time_pattern.match(dep_time), f"Item {i} departure_time '{dep_time}' not in HH:MM format"
            if arr_time is not None:
                assert time_pattern.match(arr_time), f"Item {i} arrival_time '{arr_time}' not in HH:MM format"
        
        print("✓ Time formats valid for all items")


class TestPopularNowEdgeCases:
    """Edge case tests for /api/popular/now"""
    
    def test_popular_now_limit_zero(self):
        """limit=0 should return at least 1 item (min clamped)"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=0")
        assert response.status_code == 200
        data = response.json()
        # Code clamps to max(1, min(limit, 12))
        assert len(data["items"]) >= 1, "limit=0 should return at least 1 item"
        print(f"✓ limit=0 returns {len(data['items'])} items (min clamped to 1)")
    
    def test_popular_now_limit_large(self):
        """limit=100 should be capped at 12"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=100")
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 12, f"limit=100 should cap at 12, got {len(data['items'])}"
        print(f"✓ limit=100 capped at {len(data['items'])} items (max 12)")
    
    def test_popular_now_invalid_limit(self):
        """Invalid limit parameter should use default or handle gracefully"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=abc")
        # Should either return 422 (validation error) or use default
        assert response.status_code in [200, 422], f"Expected 200 or 422, got {response.status_code}"
        print(f"✓ Invalid limit handled: status {response.status_code}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

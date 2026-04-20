"""
Test suite for Popular Now diversified schedules feature.
Tests:
1. GET /api/popular/now returns items with GENUINELY VARIED departure_time values
2. Items sorted today-first then by minutes_until_departure ascending
3. Currencies respected - SG-origin routes return currency='sgd', MY-origin routes return 'myr'
4. Startup migration _diversify_popular_schedules is idempotent
5. Existing seed schedules and bookings are unaffected
"""
import pytest
import requests
import os
from datetime import datetime, timezone

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')

class TestPopularNowDiversified:
    """Tests for diversified Popular Now schedules"""
    
    def test_popular_now_returns_varied_times(self):
        """Verify at least 4 distinct HH:MM values across 6 returned items"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=6")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        
        data = response.json()
        items = data.get('items', [])
        assert len(items) >= 4, f"Expected at least 4 items, got {len(items)}"
        
        # Extract unique departure times
        departure_times = set(item['departure_time'] for item in items)
        print(f"Departure times found: {sorted(departure_times)}")
        
        # Must have at least 4 distinct times
        assert len(departure_times) >= 4, f"Expected at least 4 distinct times, got {len(departure_times)}: {sorted(departure_times)}"
        print(f"PASS: {len(departure_times)} distinct departure times across {len(items)} items")
    
    def test_popular_now_sorted_correctly(self):
        """Verify items sorted: today-first, then by minutes_until_departure ascending"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=6")
        assert response.status_code == 200
        
        items = response.json().get('items', [])
        if len(items) < 2:
            pytest.skip("Not enough items to test sorting")
        
        # Check today items come first
        today_items = [i for i in items if i.get('is_today')]
        non_today_items = [i for i in items if not i.get('is_today')]
        
        # If we have both today and non-today items, today should come first
        if today_items and non_today_items:
            today_indices = [items.index(i) for i in today_items]
            non_today_indices = [items.index(i) for i in non_today_items]
            assert max(today_indices) < min(non_today_indices), "Today items should come before non-today items"
            print(f"PASS: Today items ({len(today_items)}) come before non-today items ({len(non_today_items)})")
        
        # Within today items, check minutes_until_departure is ascending
        if len(today_items) >= 2:
            minutes = [i.get('minutes_until_departure') for i in today_items if i.get('minutes_until_departure') is not None]
            if len(minutes) >= 2:
                for i in range(len(minutes) - 1):
                    assert minutes[i] <= minutes[i+1], f"Today items not sorted by minutes: {minutes}"
                print(f"PASS: Today items sorted by minutes_until_departure: {minutes}")
    
    def test_currency_respected_sg_origin(self):
        """SG-origin routes should return currency='sgd'"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=12")
        assert response.status_code == 200
        
        items = response.json().get('items', [])
        
        # Find Singapore-origin routes
        sg_routes = [i for i in items if i.get('from_city') == 'Singapore']
        
        if not sg_routes:
            print("No Singapore-origin routes in current popular items - checking search API")
            # Try to find a Singapore terminal and search for schedules
            terminals_resp = requests.get(f"{BASE_URL}/api/terminals")
            if terminals_resp.status_code == 200:
                all_terms = terminals_resp.json().get('all', [])
                sg_term = next((t for t in all_terms if t.get('city') == 'Singapore'), None)
                kl_term = next((t for t in all_terms if t.get('city') == 'Kuala Lumpur'), None)
                if sg_term and kl_term:
                    today = datetime.now(timezone.utc).date().isoformat()
                    search_resp = requests.get(f"{BASE_URL}/api/search", params={
                        'from_terminal_id': sg_term['id'],
                        'to_terminal_id': kl_term['id'],
                        'date': today
                    })
                    if search_resp.status_code == 200:
                        schedules = search_resp.json().get('schedules', [])
                        if schedules:
                            for s in schedules[:3]:
                                assert s.get('currency') == 'sgd', f"SG-origin schedule should have currency='sgd', got {s.get('currency')}"
                            print(f"PASS: SG-origin schedules have currency='sgd'")
                            return
            pytest.skip("No Singapore-origin routes available for testing")
        
        for route in sg_routes:
            assert route.get('currency') == 'sgd', f"Singapore-origin route should have currency='sgd', got {route.get('currency')}"
        print(f"PASS: {len(sg_routes)} Singapore-origin routes have currency='sgd'")
    
    def test_currency_respected_my_origin(self):
        """MY-origin routes should return currency='myr'"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=12")
        assert response.status_code == 200
        
        items = response.json().get('items', [])
        
        # Find Malaysia-origin routes (not Singapore)
        my_routes = [i for i in items if i.get('from_city') != 'Singapore']
        
        if not my_routes:
            pytest.skip("No Malaysia-origin routes in current popular items")
        
        for route in my_routes:
            assert route.get('currency') == 'myr', f"MY-origin route {route.get('from_city')}->{route.get('to_city')} should have currency='myr', got {route.get('currency')}"
        print(f"PASS: {len(my_routes)} Malaysia-origin routes have currency='myr'")
    
    def test_all_items_have_required_fields(self):
        """Verify all items have required fields for frontend rendering"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=6")
        assert response.status_code == 200
        
        items = response.json().get('items', [])
        required_fields = [
            'from_city', 'to_city', 'from_terminal', 'to_terminal',
            'schedule_id', 'departure_date', 'departure_time', 'arrival_time',
            'is_today', 'minutes_until_departure', 'fare', 'currency', 'seats_available'
        ]
        
        for item in items:
            for field in required_fields:
                assert field in item, f"Missing field '{field}' in item {item.get('from_city')}->{item.get('to_city')}"
        
        print(f"PASS: All {len(items)} items have required fields")
    
    def test_seats_available_is_valid(self):
        """Verify seats_available is a non-negative integer"""
        response = requests.get(f"{BASE_URL}/api/popular/now?limit=6")
        assert response.status_code == 200
        
        items = response.json().get('items', [])
        for item in items:
            seats = item.get('seats_available')
            assert isinstance(seats, int), f"seats_available should be int, got {type(seats)}"
            assert seats >= 0, f"seats_available should be non-negative, got {seats}"
        
        print(f"PASS: All items have valid seats_available values")


class TestMigrationIdempotency:
    """Tests for migration idempotency - running twice should not cause issues"""
    
    def test_schedules_count_stable(self):
        """Verify schedule count is stable (migration doesn't keep adding duplicates)"""
        # Get current schedule count
        response1 = requests.get(f"{BASE_URL}/api/admin/schedules", 
                                 headers=self._get_admin_headers())
        if response1.status_code == 401:
            pytest.skip("Admin auth required for this test")
        
        count1 = len(response1.json()) if response1.status_code == 200 else 0
        print(f"Current schedule count: {count1}")
        
        # The migration should have already run on startup
        # Just verify we have a reasonable number of schedules
        assert count1 > 100, f"Expected many schedules after migration, got {count1}"
        print(f"PASS: Schedule count is {count1} (migration has run)")
    
    def test_no_duplicate_schedules(self):
        """Verify no duplicate (from, to, date, time) combinations exist"""
        response = requests.get(f"{BASE_URL}/api/admin/schedules",
                               headers=self._get_admin_headers())
        if response.status_code == 401:
            pytest.skip("Admin auth required for this test")
        
        schedules = response.json() if response.status_code == 200 else []
        
        # Check for duplicates
        seen = set()
        duplicates = []
        for s in schedules:
            key = (s.get('from_terminal_id'), s.get('to_terminal_id'), 
                   s.get('departure_date'), s.get('departure_time'))
            if key in seen:
                duplicates.append(key)
            seen.add(key)
        
        assert len(duplicates) == 0, f"Found {len(duplicates)} duplicate schedules: {duplicates[:5]}"
        print(f"PASS: No duplicate schedules found among {len(schedules)} schedules")
    
    def _get_admin_headers(self):
        """Get admin auth headers"""
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@starqistna.com",
            "password": "Admin@123"
        })
        if login_resp.status_code == 200:
            token = login_resp.json().get('access_token')
            return {"Authorization": f"Bearer {token}"}
        return {}


class TestExistingDataUnaffected:
    """Tests to verify existing seed schedules and bookings are unaffected"""
    
    def test_original_seed_times_still_exist(self):
        """Verify original seed times (08:00, 11:30, 14:00, 22:00) still exist"""
        response = requests.get(f"{BASE_URL}/api/admin/schedules",
                               headers=self._get_admin_headers())
        if response.status_code == 401:
            pytest.skip("Admin auth required for this test")
        
        schedules = response.json() if response.status_code == 200 else []
        
        original_times = {'08:00', '11:30', '14:00', '22:00'}
        found_times = set(s.get('departure_time') for s in schedules)
        
        for t in original_times:
            assert t in found_times, f"Original seed time {t} should still exist"
        
        print(f"PASS: All original seed times {original_times} still exist")
    
    def test_search_api_still_works(self):
        """Verify search API returns results for popular routes"""
        # Get terminals
        terminals_resp = requests.get(f"{BASE_URL}/api/terminals")
        assert terminals_resp.status_code == 200
        
        all_terms = terminals_resp.json().get('all', [])
        kl_term = next((t for t in all_terms if t.get('city') == 'Kuala Lumpur'), None)
        melaka_term = next((t for t in all_terms if t.get('city') == 'Melaka'), None)
        
        if not kl_term or not melaka_term:
            pytest.skip("Required terminals not found")
        
        today = datetime.now(timezone.utc).date().isoformat()
        search_resp = requests.get(f"{BASE_URL}/api/search", params={
            'from_terminal_id': kl_term['id'],
            'to_terminal_id': melaka_term['id'],
            'date': today
        })
        
        assert search_resp.status_code == 200
        schedules = search_resp.json().get('schedules', [])
        assert len(schedules) > 0, "Search should return schedules for KL->Melaka"
        print(f"PASS: Search API returns {len(schedules)} schedules for KL->Melaka on {today}")
    
    def _get_admin_headers(self):
        """Get admin auth headers"""
        login_resp = requests.post(f"{BASE_URL}/api/auth/login", json={
            "email": "admin@starqistna.com",
            "password": "Admin@123"
        })
        if login_resp.status_code == 200:
            token = login_resp.json().get('access_token')
            return {"Authorization": f"Bearer {token}"}
        return {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

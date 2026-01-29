from locust import HttpUser, task, between
from datetime import date, timedelta
import random

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
STAGING_EMAIL = "admin@staging.com"
STAGING_PASSWORD = "StagingPassword123!"

class SiteSpaceUser(HttpUser):
    wait_time = between(1, 3)
    
    token = None
    project_ids = []
    asset_ids = []

    def on_start(self):
        """ Login on startup """
        # NOTE: Check if login endpoint also needs a trailing slash based on your API docs
        login_url = "/api/auth/login" 
        print(f"🔄 Attempting login as {STAGING_EMAIL}...")
        
        response = self.client.post(login_url, json={
            "email": STAGING_EMAIL,
            "password": STAGING_PASSWORD
        })

        if response.status_code == 200:
            try:
                self.token = response.json().get("access_token")
                print(f"✅ Login Successful!")
            except:
                self.token = None
        else:
            print(f"❌ Login Failed ({response.status_code}): {response.text}")

    @task(3)
    def get_projects(self):
        """
        Fetch Projects.
        """
        if not self.token: return

        # FIX: Added trailing slash '/' before '?' and default skip/limit
        url = "/api/projects/?my_projects=true&skip=0&limit=100"
        
        with self.client.get(url, 
                             headers={"Authorization": f"Bearer {self.token}"},
                             catch_response=True) as response:
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    projects = data.get("projects", []) if isinstance(data, dict) else data
                    if projects:
                        self.project_ids = [p['id'] for p in projects]
                    response.success()
                except:
                    response.failure("JSON Parse Error")
            elif response.status_code == 403:
                response.failure(f"403 Forbidden - Check URL Trailing Slash. URL used: {url}")
            else:
                response.failure(f"Error {response.status_code}: {response.text}")

    @task(1)
    def get_assets(self):
        """
        Fetch Assets.
        """
        if not self.token: return
        
        if not self.project_ids:
            return

        target_project = random.choice(self.project_ids)
        
        # FIX: Added trailing slash '/' here as well to be safe
        url = f"/api/assets/?project_id={target_project}"

        with self.client.get(url, 
                             headers={"Authorization": f"Bearer {self.token}"},
                             catch_response=True) as response:
            if response.status_code == 200:
                try:
                    data = response.json()
                    assets = data.get("assets", []) if isinstance(data, dict) else data
                    if assets:
                        self.asset_ids = [a['id'] for a in assets]
                    response.success()
                except:
                    response.failure("JSON Parse Error")
            else:
                response.failure(f"Asset Error {response.status_code}: {response.text}")

    @task(1)
    def create_dynamic_booking(self):
        """ Create a booking """
        if not self.token or not self.project_ids or not self.asset_ids:
            return

        random_project = random.choice(self.project_ids)
        random_asset = random.choice(self.asset_ids)
        future_date = date.today() + timedelta(days=random.randint(1, 60))

        payload = {
            "project_id": random_project,
            "asset_id": random_asset,
            "booking_date": str(future_date),
            "start_time": "09:00",
            "end_time": "17:00",
            "purpose": "Locust Load Test",
            "notes": "Automated test"
        }

        # FIX: Ensure trailing slash is here too
        with self.client.post("/api/bookings/",
                              headers={"Authorization": f"Bearer {self.token}"},
                              json=payload,
                              catch_response=True) as response:
            if response.status_code in [200, 201, 409]:
                response.success()
            else:
                response.failure(f"Booking Error {response.status_code}: {response.text}")
# NBA Matchup Lookup

Type a player and an opponent to see head to head history, averages vs that team, recent form, a prop line hit rate checker, and today's injury report for both teams. Data comes from ESPN's free public feeds.

## Put it online (about 10 minutes, free)

### Step 1: Make a GitHub account
1. Go to github.com and click **Sign up**.
2. Verify your email.

### Step 2: Upload the files
1. On GitHub, click the **+** in the top right, then **New repository**.
2. Name it `matchup-lookup`, keep it **Public**, and click **Create repository**.
3. On the next page, click the link that says **uploading an existing file**.
4. Drag in `app.py`, `requirements.txt`, and this `README.md`.
5. Click **Commit changes**.

### Step 3: Turn it into a website
1. Go to share.streamlit.io and click **Continue with GitHub**. Approve the access it asks for.
2. Click **Create app**, then choose the option to deploy from GitHub.
3. Fill in:
   * Repository: `your-username/matchup-lookup`
   * Branch: `main`
   * Main file path: `app.py`
   * App URL: pick something you like, for example `anish-matchups`
4. Click **Deploy**. The first build takes 2 to 3 minutes.
5. Copy the link from your browser and send it to your friends.

## Good to know
* **First search is slow** (around 10 seconds) because it loads every NBA roster. After that it's quick.
* **Free apps nap** when nobody uses them for a while. If you see a "wake up" button, click it and wait about 30 seconds.
* **Injury reports** refresh every 15 minutes. Game logs refresh every 3 hours.
* If a player was just traded or signed, he may take a few hours to show up under his new team.

## Making changes
1. Open `app.py` on GitHub and click the pencil icon to edit.
2. Change something and click **Commit changes**.
3. Your website updates by itself within a minute.

Easy things to try first (near the top of `app.py`):
* `SEASONS_BACK = 4` controls how many seasons of history to pull
* `LAST_N_GAMES = 10` controls the size of the recent form table
* `STATS_TO_SHOW` controls which stats appear

## If something breaks
ESPN doesn't officially support these feeds, so they can change without notice. If the app shows an error, copy the error message and paste it to Claude.

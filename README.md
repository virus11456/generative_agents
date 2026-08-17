

# Generative Agents: Interactive Simulacra of Human Behavior 

<p align="center" width="100%">
<img src="cover.png" alt="Smallville" style="width: 80%; min-width: 300px; display: block; margin: auto;">
</p>

This repository accompanies our research paper titled "[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)." It contains our core simulation module for  generative agents—computational agents that simulate believable human behaviors—and their game environment. Below, we document the steps for setting up the simulation environment on your local machine and for replaying the simulation as a demo animation.

---

# 新版功能說明（中文）

這個 fork 將原始的 Stanford 程式碼全面現代化，讓它在今天可以直接跑起來，並加上省錢提速與新功能。以下是完整說明。

## 新功能總覽

| 功能 | 說明 |
|---|---|
| 新版 OpenAI SDK | 全面遷移到 `openai>=1.0`；已下架的 `text-davinci-003` 等模型自動改由現行模型服務（預設 `gpt-4o-mini`，可自訂） |
| 多供應商支援 | 內建 OpenAI (ChatGPT)、DeepSeek、MiniMax、Gemini、Ollama（本地模型）預設，也支援任何 OpenAI 相容端點 |
| 網頁設定 API Key | 瀏覽器開 `/settings/` 頁面即可選供應商、輸入 key、選模型，不用改任何程式碼 |
| LLM 回應快取 | 相同的 prompt 直接從本機 sqlite 快取回應，大幅節省 API 費用；重跑實驗幾乎免費 |
| 平行化模擬 | 每一步所有 agent 的思考併發執行（原版是逐一排隊），多人模擬速度大幅提升 |
| 自動存檔 | 每 50 步自動存檔 + 崩潰時緊急存檔，API 逾時不再毀掉整場模擬 |
| 費用統計 | 隨時輸入 `stats` 查看各模型 token 用量與預估花費 |
| 玩家介入 | `whisper` 植入想法、`interview` 與 agent 對話，主動影響劇情走向；也有網頁版 `/intervene/` 面板 |
| VPS 部署 | 支援公網部署，`ALLOWED_HOSTS`、設定頁 token 保護等都已備妥 |
| Docker 一鍵部署 | `docker compose up -d` 啟動網頁伺服器、`docker compose run --rm backend` 啟動模擬，免裝 Python 環境 |
| 成本上限保護 | 設 `COST_LIMIT_USD` 預算上限，花費達 80% 警告、達 100% 自動存檔停止，防止掛機跑出天價帳單 |
| 劇本產生器 | 一句話的故事設定（中英文皆可）→ 自動生成所有角色的人設、目標、人際關係，開一場全新劇本的模擬 |

## 快速開始

```bash
# 1. 安裝依賴（需要 Python 3.11+）
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. 啟動環境伺服器（第一個終端機）
cd environment/frontend_server
python manage.py runserver

# 3. 啟動模擬伺服器（第二個終端機）
cd reverie/backend_server
python reverie.py
```

啟動 `reverie.py` 之前，要先設定好 API key（見下一節）。之後照原版流程：輸入 fork 來源 `base_the_ville_isabella_maria_klaus`、取一個新模擬名稱，瀏覽器開 [http://localhost:8000/simulator_home](http://localhost:8000/simulator_home)，在 `Enter option:` 輸入 `run 100` 開跑。

## 設定 API Key（兩種方式）

### 方式一：網頁設定頁（推薦）
環境伺服器啟動後，瀏覽器開 [http://localhost:8000/settings/](http://localhost:8000/settings/)：

1. 從下拉選單選擇供應商——**OpenAI (ChatGPT)、DeepSeek、MiniMax、Gemini、Ollama、自訂**——端點網址和預設模型會自動填入
2. 貼上你的 API key
3. 按「Save」存檔，然後（重）啟動 `reverie.py` 生效

設定會存到專案根目錄的 `llm_config.json`（檔案權限 600、已加入 `.gitignore`、頁面永遠不會回顯完整 key）。此檔案的設定**優先於環境變數**。

**Embedding 注意事項**：DeepSeek 和 MiniMax 沒有 OpenAI 相容的 embedding API，所以選這兩家時，設定頁的 Embedding 區塊預設指向 OpenAI——你需要在該區塊另外填一組 OpenAI（或 Gemini / Ollama）的 key。Ollama 和 Gemini 則聊天與 embedding 都可以用同一家。

### 方式二：環境變數（或 `.env` 檔）
在 `reverie/backend_server` 目錄建立 `.env` 檔（或直接 export）：

```
OPENAI_API_KEY=你的key
OPENAI_BASE_URL=            # 留空 = OpenAI 官方；DeepSeek 填 https://api.deepseek.com
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=          # embedding 用不同供應商時才需要
EMBEDDING_BASE_URL=
```

## 效能相關環境變數

| 變數 | 預設 | 說明 |
|---|---|---|
| `LLM_CACHE` | `1` | 設 `0` 關閉 LLM 回應快取 |
| `LLM_CACHE_PATH` | 自動 | 快取 sqlite 檔案位置 |
| `PARALLEL_PERSONAS` | `1` | 設 `0` 改回逐一執行 agent |
| `MAX_PARALLEL_WORKERS` | `8` | 平行執行的執行緒數 |
| `CHECKPOINT_FREQ` | `50` | 每 N 步自動存檔，設 `0` 關閉 |
| `COST_LIMIT_USD` | `0`（關閉） | 預估花費達此金額（美元）時自動存檔並停止模擬；達 80% 先警告 |
| `REVERIE_DEBUG` | `0` | 設 `1` 顯示完整 prompt 除錯輸出 |

## 模擬指令一覽

在模擬伺服器的 `Enter option:` 提示符輸入：

| 指令 | 功能 |
|---|---|
| `run <步數>` | 跑指定步數（1 步 = 遊戲內 10 秒） |
| `save` | 存檔（不退出） |
| `fin` | 存檔並退出 |
| `exit` | 不存檔退出（會刪除本場模擬） |
| `stats` | 顯示各模型 token 用量與預估費用 |
| `whisper <角色名>: <想法>` | **玩家介入**：把一個想法植入 agent 的記憶流，會影響它之後的計畫與對話。例：`whisper Isabella Rodriguez: I am planning a party tonight`（需先跑至少 1 步） |
| `interview <角色名>` | 與 agent 即時對話（無痕，不寫入記憶）。例：`interview Klaus Mueller` |
| `help` | 完整指令列表 |

**玩家介入玩法提示**：論文中著名的「情人節派對」湧現劇情，就是用 whisper 機制植入一條記憶引發的——你可以 whisper 給某個 agent「今晚要辦派對」，然後觀察消息如何在小鎮傳開、誰會赴約。

費用統計除了 `stats` 指令外，每次存檔也會寫入該模擬資料夾的 `reverie/llm_stats.json`。

### 網頁版介入面板
不想切到終端機的話，瀏覽器開 [http://localhost:8000/intervene/](http://localhost:8000/intervene/)：下拉選擇 agent、輸入想植入的想法（以「你」的口吻，例如 "You are planning a party tonight"），按下 Whisper 即排入佇列，模擬跑到下一步之間會自動注入。適合邊看 `simulator_home` 地圖邊介入劇情，展示效果極佳。公網部署時同樣受 `SETTINGS_TOKEN` 保護。

## 劇本產生器：一句話開一場新戲

用一段故事設定（**中英文都可以**）自動改寫所有角色的人設、目標與人際關係：

```bash
cd reverie/backend_server
python scenario_generator.py \
  --fork base_the_ville_isabella_maria_klaus \
  --name startup_drama \
  --story "三個室友：一人偷偷計畫創業想挖另外兩人入夥，一人正準備出國留學，一人剛失業還瞞著大家"
```

工具會為每個角色呼叫 LLM 生成新的個性、背景、近況、作息與日常安排（寫回模擬資料），並產出一份人際關係「耳語」CSV。之後照畫面提示執行：

```
python reverie.py
  Enter the name of the forked simulation: startup_drama
  Enter the name of the new simulation: startup_drama_run1
  Enter option: run 1
  Enter option: call -- load history the_ville/scenario_startup_drama.csv
  Enter option: run 1000
```

用 `--fork base_the_ville_n25` 可以生成 25 人的大型劇本。角色名字與地圖不變（沿用原有精靈圖與空間資料），變的是他們的靈魂。

## 成本上限保護

怕掛機跑出天價帳單？設定預算上限（美元）：

```bash
COST_LIMIT_USD=5 python reverie.py
```

預估花費達 80% 時印出警告；達 100% 時自動存檔並停止模擬（進度不會丟，fork 存檔即可續跑）。預設 `0` = 不限制。

## Docker 一鍵部署

裝好 [Docker](https://docs.docker.com/engine/install/) 之後，在專案根目錄：

```bash
# 啟動網頁（環境）伺服器，背景執行於 8000 埠
docker compose up -d

# 啟動互動式模擬伺服器
docker compose run --rm backend
```

環境變數可寫在專案根目錄的 `.env` 檔（`OPENAI_API_KEY`、`COST_LIMIT_USD`、`SETTINGS_TOKEN`、`ALLOWED_HOSTS`、`FRONTEND_PORT` 等 compose 都會自動帶入），或直接用 `/settings/` 網頁設定。程式碼與模擬存檔都掛載自主機目錄，資料不會因容器重啟而消失。VPS 上這就是最省事的部署方式——裝 Docker、clone、兩條指令開跑。

## 部署到 VPS

2GB RAM 的小 VPS 就足夠（重運算都發生在 LLM 供應商端）：

```bash
git clone <你的fork網址> && cd generative_agents
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 環境伺服器（8000 埠）
cd environment/frontend_server
ALLOWED_HOSTS=<你的網域或IP> SETTINGS_TOKEN=<自訂密碼> \
  gunicorn frontend_server.wsgi -b 0.0.0.0:8000 --timeout 600

# 模擬伺服器（互動式 CLI，放在 tmux 裡跑）
cd reverie/backend_server
tmux new -s reverie
python reverie.py
```

部署後：
1. 開 `http://<你的VPS>:8000/settings/?token=<自訂密碼>` 輸入 API key
2. 開 `http://<你的VPS>:8000/simulator_home` 觀看模擬——**跑模擬時要保持這個分頁開啟**（畫面步進由瀏覽器驅動）
3. 回放：`http://<你的VPS>:8000/replay/<模擬名稱>/<起始步數>/`

**安全提醒（重要）**：本系統沒有登入驗證，放上公網請務必——

* 用防火牆把 8000 埠限制在你自己的 IP，或在前面架 nginx + basic auth
* 一定要設 `SETTINGS_TOKEN`（保護 API key 設定頁）和真實的 `ALLOWED_HOSTS`
* `llm_config.json` 內含你的 API key，注意檔案權限（系統會自動設為 600）

## 執行測試（不需要 API key）

```bash
# 後端（15 個測試）
cd reverie/backend_server && python -m unittest discover tests

# 前端（5 個測試）
cd environment/frontend_server && python manage.py test translator
```

---

## What's New in This Fork
This fork modernizes the original codebase so it runs today:

* **openai>=1.0 SDK + current models** — the retired `text-davinci-003`/`gpt-3.5-turbo` calls are served by `gpt-4o-mini` by default (configurable), and embeddings use `text-embedding-3-small`.
* **Environment-variable configuration** — no more hand-written `utils.py`; just set `OPENAI_API_KEY` (a `.env` file works too).
* **Multi-provider support** — set `OPENAI_BASE_URL` to point at any OpenAI-compatible endpoint (e.g. `http://localhost:11434/v1` for Ollama).
* **On-disk LLM + embedding cache** — repeated prompts are served from a local sqlite cache, cutting API costs substantially; disable with `LLM_CACHE=0`.
* **Parallel persona steps** — each simulation step runs all agents' cognition concurrently in a thread pool (cross-agent conversations stay serialized for safety); disable with `PARALLEL_PERSONAS=0`.
* **Auto-checkpoint & crash recovery** — the simulation saves itself every `CHECKPOINT_FREQ` steps (default 50) and on any crash, so progress can be resumed by forking the saved simulation.
* **Token/cost accounting** — type `stats` at the simulation prompt for live token usage and estimated cost; stats are also saved to `reverie/llm_stats.json` inside each simulation folder.
* **Python 3.11 + Django 4.2** — dependencies updated, retired APIs replaced, and offline unit tests added under `reverie/backend_server/tests` and `translator/tests.py`.
* **Player intervention** — `whisper <persona>: <thought>` injects a thought into an agent's memory stream mid-simulation (the paper's Valentine's-party mechanism), `interview <persona>` opens a live chat with an agent, and `help` lists every command.
* **Web-based provider settings** — visit `/settings` on the environment server to pick a provider preset (OpenAI, DeepSeek, MiniMax, Gemini, Ollama, or any OpenAI-compatible endpoint) and enter API keys in the browser; saved to a git-ignored `llm_config.json` that overrides environment variables.
* **Web intervention panel** — `/intervene/` lets you whisper a thought into any agent's mind from the browser while the simulation runs.
* **Scenario generator** — `python scenario_generator.py --name <sim> --story "<premise in any language>"` rewrites every persona's identity and relationships to fit a story premise and produces a loadable whisper CSV.
* **Cost ceiling** — set `COST_LIMIT_USD` to auto-save and halt the simulation when the estimated spend reaches your budget (warning at 80%).
* **Docker deployment** — `docker compose up -d` for the web server and `docker compose run --rm backend` for the interactive simulation; state persists via bind mounts.

## Configuring the LLM Provider from the Browser
With the environment server running, open [http://localhost:8000/settings/](http://localhost:8000/settings/). Choose a preset — OpenAI, DeepSeek, MiniMax, Gemini, or Ollama — enter your API key, and save. The settings are written to `llm_config.json` at the repository root (file mode 600, git-ignored, keys never rendered back) and take effect the next time you start `reverie.py`.

Notes:
* DeepSeek and MiniMax have no OpenAI-compatible embedding endpoint, so the presets point the embedding section at OpenAI — fill in a separate embedding key there (or point it at Ollama/Gemini instead).
* On a public server, set the `SETTINGS_TOKEN` environment variable and open the page as `/settings/?token=<value>`.

## Intervening in a Running Simulation
At the simulation server's `Enter option:` prompt:

    whisper Isabella Rodriguez: I am planning a Valentine's Day party tonight
    interview Klaus Mueller
    stats
    help

`whisper` permanently plants a thought in the agent's memory (it will shape their plans and conversations); `interview` is a stateless chat that leaves no memory trace. Run at least one step before whispering.

## Deploying on a VPS
Both servers run fine on a small VPS (2 GB RAM is enough; the heavy lifting happens at the LLM provider):

```bash
git clone <your fork> && cd generative_agents
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt

# Environment server (port 8000; use gunicorn or runserver)
cd environment/frontend_server
ALLOWED_HOSTS=<your-domain-or-ip> SETTINGS_TOKEN=<secret> \
  gunicorn frontend_server.wsgi -b 0.0.0.0:8000 --timeout 600

# Simulation server (interactive CLI -- keep it in tmux/screen)
cd reverie/backend_server
tmux new -s reverie
python reverie.py
```

Then open `http://<your-vps>:8000/settings/?token=<secret>` to enter API keys, and `http://<your-vps>:8000/simulator_home` to watch the simulation. Keep the `simulator_home` tab open while running — the browser drives the visual stepping.

Security notes for public deployments: this app has no authentication, so restrict access — firewall port 8000 to your own IP, or put nginx with basic auth in front; always set `SETTINGS_TOKEN` and a real `ALLOWED_HOSTS` value.

## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Isabella_Rodriguez.png" alt="Generative Isabella">   Setting Up the Environment 
To set up your environment, you will need to provide your OpenAI API key and download the necessary packages.

### Step 1. Configure Your API Key
Set the `OPENAI_API_KEY` environment variable, or create a `.env` file in `reverie/backend_server` (where `reverie.py` is located) containing:
```
OPENAI_API_KEY=<Your OpenAI API key>
KEY_OWNER=<Name>
```
Optional settings (see `reverie/backend_server/utils.py` for the full list):
```
OPENAI_CHAT_MODEL=gpt-4o-mini        # default chat model
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_BASE_URL=                     # e.g. http://localhost:11434/v1 for Ollama
LLM_CACHE=1                          # set 0 to disable the response cache
PARALLEL_PERSONAS=1                  # set 0 to step agents sequentially
CHECKPOINT_FREQ=50                   # auto-save every N steps
```

### Step 2. Install requirements.txt
Install everything listed in the `requirements.txt` file (I strongly recommend first setting up a virtualenv as usual). A note on Python version: this fork targets Python 3.11+ (Django 4.2, openai>=1.0).

## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Klaus_Mueller.png" alt="Generative Klaus">   Running a Simulation 
To run a new simulation, you will need to concurrently start two servers: the environment server and the agent simulation server.

### Step 1. Starting the Environment Server
Again, the environment is implemented as a Django project, and as such, you will need to start the Django server. To do this, first navigate to `environment/frontend_server` (this is where `manage.py` is located) in your command line. Then run the following command:

    python manage.py runserver

Then, on your favorite browser, go to [http://localhost:8000/](http://localhost:8000/). If you see a message that says, "Your environment server is up and running," your server is running properly. Ensure that the environment server continues to run while you are running the simulation, so keep this command-line tab open! (Note: I recommend using either Chrome or Safari. Firefox might produce some frontend glitches, although it should not interfere with the actual simulation.)

### Step 2. Starting the Simulation Server
Open up another command line (the one you used in Step 1 should still be running the environment server, so leave that as it is). Navigate to `reverie/backend_server` and run `reverie.py`.

    python reverie.py
This will start the simulation server. A command-line prompt will appear, asking the following: "Enter the name of the forked simulation: ". To start a 3-agent simulation with Isabella Rodriguez, Maria Lopez, and Klaus Mueller, type the following:
    
    base_the_ville_isabella_maria_klaus
The prompt will then ask, "Enter the name of the new simulation: ". Type any name to denote your current simulation (e.g., just "test-simulation" will do for now).

    test-simulation
Keep the simulator server running. At this stage, it will display the following prompt: "Enter option: "

### Step 3. Running and Saving the Simulation
On your browser, navigate to [http://localhost:8000/simulator_home](http://localhost:8000/simulator_home). You should see the map of Smallville, along with a list of active agents on the map. You can move around the map using your keyboard arrows. Please keep this tab open. To run the simulation, type the following command in your simulation server in response to the prompt, "Enter option":

    run <step-count>
Note that you will want to replace `<step-count>` above with an integer indicating the number of game steps you want to simulate. For instance, if you want to simulate 100 game steps, you should input `run 100`. One game step represents 10 seconds in the game.


Your simulation should be running, and you will see the agents moving on the map in your browser. Once the simulation finishes running, the "Enter option" prompt will re-appear. At this point, you can simulate more steps by re-entering the run command with your desired game steps, exit the simulation without saving by typing `exit`, or save and exit by typing `fin`.

The saved simulation can be accessed the next time you run the simulation server by providing the name of your simulation as the forked simulation. This will allow you to restart your simulation from the point where you left off.

### Step 4. Replaying a Simulation
You can replay a simulation that you have already run simply by having your environment server running and navigating to the following address in your browser: `http://localhost:8000/replay/<simulation-name>/<starting-time-step>`. Please make sure to replace `<simulation-name>` with the name of the simulation you want to replay, and `<starting-time-step>` with the integer time-step from which you wish to start the replay.

For instance, by visiting the following link, you will initiate a pre-simulated example, starting at time-step 1:  
[http://localhost:8000/replay/July1_the_ville_isabella_maria_klaus-step-3-20/1/](http://localhost:8000/replay/July1_the_ville_isabella_maria_klaus-step-3-20/1/)

### Step 5. Demoing a Simulation
You may have noticed that all character sprites in the replay look identical. We would like to clarify that the replay function is primarily intended for debugging purposes and does not prioritize optimizing the size of the simulation folder or the visuals. To properly demonstrate a simulation with appropriate character sprites, you will need to compress the simulation first. To do this, open the `compress_sim_storage.py` file located in the `reverie` directory using a text editor. Then, execute the `compress` function with the name of the target simulation as its input. By doing so, the simulation file will be compressed, making it ready for demonstration.

To start the demo, go to the following address on your browser: `http://localhost:8000/demo/<simulation-name>/<starting-time-step>/<simulation-speed>`. Note that `<simulation-name>` and `<starting-time-step>` denote the same things as mentioned above. `<simulation-speed>` can be set to control the demo speed, where 1 is the slowest, and 5 is the fastest. For instance, visiting the following link will start a pre-simulated example, beginning at time-step 1, with a medium demo speed:  
[http://localhost:8000/demo/July1_the_ville_isabella_maria_klaus-step-3-20/1/3/](http://localhost:8000/demo/July1_the_ville_isabella_maria_klaus-step-3-20/1/3/)

### Tips
We've noticed that OpenAI's API can hang when it reaches the hourly rate limit. When this happens, you may need to restart your simulation. For now, we recommend saving your simulation often as you progress to ensure that you lose as little of the simulation as possible when you do need to stop and rerun it. Running these simulations, at least as of early 2023, could be somewhat costly, especially when there are many agents in the environment.

## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Maria_Lopez.png" alt="Generative Maria">   Simulation Storage Location
All simulations that you save will be located in `environment/frontend_server/storage`, and all compressed demos will be located in `environment/frontend_server/compressed_storage`. 

## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Sam_Moore.png" alt="Generative Sam">   Customization

There are two ways to optionally customize your simulations. 

### Author and Load Agent History
First is to initialize agents with unique history at the start of the simulation. To do this, you would want to 1) start your simulation using one of the base simulations, and 2) author and load agent history. More specifically, here are the steps:

#### Step 1. Starting Up a Base Simulation 
There are two base simulations included in the repository: `base_the_ville_n25` with 25 agents, and `base_the_ville_isabella_maria_klaus` with 3 agents. Load one of the base simulations by following the steps until step 2 above. 

#### Step 2. Loading a History File 
Then, when prompted with "Enter option: ", you should load the agent history by responding with the following command:

    call -- load history the_ville/<history_file_name>.csv
Note that you will need to replace `<history_file_name>` with the name of an existing history file. There are two history files included in the repo as examples: `agent_history_init_n25.csv` for `base_the_ville_n25` and `agent_history_init_n3.csv` for `base_the_ville_isabella_maria_klaus`. These files include semicolon-separated lists of memory records for each of the agents—loading them will insert the memory records into the agents' memory stream.

#### Step 3. Further Customization 
To customize the initialization by authoring your own history file, place your file in the following folder: `environment/frontend_server/static_dirs/assets/the_ville`. The column format for your custom history file will have to match the example history files included. Therefore, we recommend starting the process by copying and pasting the ones that are already in the repository.

### Create New Base Simulations
For a more involved customization, you will need to author your own base simulation files. The most straightforward approach would be to copy and paste an existing base simulation folder, renaming and editing it according to your requirements. This process will be simpler if you decide to keep the agent names unchanged. However, if you wish to change their names or increase the number of agents that the Smallville map can accommodate, you might need to directly edit the map using the [Tiled](https://www.mapeditor.org/) map editor.


## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Eddy_Lin.png" alt="Generative Eddy">   Authors and Citation 

**Authors:** Joon Sung Park, Joseph C. O'Brien, Carrie J. Cai, Meredith Ringel Morris, Percy Liang, Michael S. Bernstein

Please cite our paper if you use the code or data in this repository. 
```
@inproceedings{Park2023GenerativeAgents,  
author = {Park, Joon Sung and O'Brien, Joseph C. and Cai, Carrie J. and Morris, Meredith Ringel and Liang, Percy and Bernstein, Michael S.},  
title = {Generative Agents: Interactive Simulacra of Human Behavior},  
year = {2023},  
publisher = {Association for Computing Machinery},  
address = {New York, NY, USA},  
booktitle = {In the 36th Annual ACM Symposium on User Interface Software and Technology (UIST '23)},  
keywords = {Human-AI interaction, agents, generative AI, large language models},  
location = {San Francisco, CA, USA},  
series = {UIST '23}
}
```

## <img src="https://joonsungpark.s3.amazonaws.com:443/static/assets/characters/profile/Wolfgang_Schulz.png" alt="Generative Wolfgang">   Acknowledgements

We encourage you to support the following three amazing artists who have designed the game assets for this project, especially if you are planning to use the assets included here for your own project: 
* Background art: [PixyMoon (@_PixyMoon\_)](https://twitter.com/_PixyMoon_)
* Furniture/interior design: [LimeZu (@lime_px)](https://twitter.com/lime_px)
* Character design: [ぴぽ (@pipohi)](https://twitter.com/pipohi)

In addition, we thank Lindsay Popowski, Philip Guo, Michael Terry, and the Center for Advanced Study in the Behavioral Sciences (CASBS) community for their insights, discussions, and support. Lastly, all locations featured in Smallville are inspired by real-world locations that Joon has frequented as an undergraduate and graduate student---he thanks everyone there for feeding and supporting him all these years.



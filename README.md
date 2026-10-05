# Project Setup

Follow these steps to set up and run the project.

## 1. Create a Python Virtual Environment

It is recommended to use a virtual environment to isolate project dependencies.

### On Linux/macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### On Windows:
```bash
python -m venv .venv
.venv\Scripts\activate
```

## 2. Install Dependencies

Once the virtual environment is activated, install the required packages:

```bash
pip install -r requirements.txt
```

## 3. Configure Environment Variables

The project requires credentials for Neo4j and OpenAI. 

1. Copy the template file:
   ```bash
   cp .env.example .env
   ```
2. Open the `.env` file and fill in your actual credentials:
   - `NEO4J_URL`: Your Neo4j instance URL (e.g., `bolt://localhost:7687`)
   - `NEO4J_USERNAME`: Your Neo4j username
   - `NEO4J_PASSWORD`: Your Neo4j password
   - `NEO4J_DATABASE`: Your Neo4j database name (e.g., `neo4j`)
   - `OPENAI_API_KEY`: Your OpenAI API key

**Important:** Never commit your `.env` file to version control. It is already included in `.gitignore`.

## 4. Run the Project

You can now run the main script:

```bash
python main.py
```
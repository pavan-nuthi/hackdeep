# Blaxel Swagger Finder

A tool to scan GitHub repositories for Swagger/OpenAPI documentation files using Blaxel Sandboxes.

## Features

-   **Secure Scanning**: Uses Blaxel Sandboxes to clone and analyze repositories in a secure, isolated environment.
-   **CLI Support**: Run scans directly from the command line.
-   **Web UI**: User-friendly interface built with Streamlit.
-   **Bulk Scanning**: Scan multiple repositories at once.

## Prerequisites

-   Python 3.8+
-   [Blaxel CLI](https://docs.blaxel.ai/) installed and authenticated.

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/YOUR_USERNAME/blaxel-swagger-finder.git
    cd blaxel-swagger-finder
    ```

2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

## Usage

### Command Line Interface (CLI)

Scan a single repository:
```bash
python main.py --repo https://github.com/swagger-api/swagger-petstore
```

Scan multiple repositories from a file:
```bash
python main.py --file repos.txt --output results.txt
```

### Web Interface

Run the Streamlit app:
```bash
streamlit run app.py
```

Open your browser to the URL displayed in the terminal (usually `http://localhost:8501`).

## How it Works

The tool creates a `SyncSandboxInstance` using the Blaxel SDK. Inside the sandbox:
1.  It clones the target repository.
2.  It uses the `find` command to search for common OpenAPI/Swagger filenames (`swagger.json`, `swagger.yaml`, `openapi.json`, `openapi.yaml`).
3.  Results are returned to the host machine.
4.  The sandbox is automatically cleaned up.

## License

MIT

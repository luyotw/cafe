# Contributing to The CAFE Engine

We warmly welcome and appreciate you considering contributing to The CAFE Engine! This document will guide you through the contribution process.

## How to Contribute

You can contribute to this project in several ways:

*   **Reporting Bugs**: If you find a bug, please create an Issue.
*   **Suggesting Features**: If you have an idea for a new feature, please create an Issue to discuss it.
*   **Submitting Pull Requests**: If you want to fix a bug or implement a feature directly.

## Development Setup

1.  **Fork & Clone**
    *   Fork this repository.
    *   Clone your fork to your local machine: `git clone https://github.com/YOUR_USERNAME/cafe.git`

2.  **Create a Virtual Environment and Install Dependencies**
    ```bash
    # Navigate to the project directory
    cd cafe

    # Create a Python virtual environment (Python 3.11+)
    python3.11 -m venv venv
    source venv/bin/activate

    # Install project dependencies (including development tools)
    pip install -e ".[dev]"
    ```

3.  **Set Up Git Hooks**
    This project uses pre-commit hooks to ensure code quality. Please run the following command to install them:
    ```bash
    ./setup-hooks.sh
    ```

## Pull Request Process

1.  Create a new feature branch from the `main` branch:
    ```bash
    git checkout -b your-feature-name
    ```

2.  Make your code changes.

3.  Run tests to ensure everything is working correctly:
    ```bash
    pytest
    ```

4.  Commit your changes. Please write a clear commit message.

5.  Push your feature branch to your fork:
    ```bash
    git push origin your-feature-name
    ```

6.  Open a Pull Request to the `main` branch of the original repository. In the PR description, please detail your changes, their purpose, and any relevant Issue numbers.

## Coding Style

*   This project follows the [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide.
*   We use pre-commit hooks to automatically check and format code. Please ensure you have them set up before committing.

## Code of Conduct

All participants in this project are expected to adhere to our [Code of Conduct](CODE_OF_CONDUCT.md). Please take the time to read it to ensure we can maintain a friendly and respectful community environment.

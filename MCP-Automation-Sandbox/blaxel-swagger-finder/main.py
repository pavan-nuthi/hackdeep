import argparse
import logging
import sys
from scanner import Scanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Blaxel Swagger Finder")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", help="GitHub repository URL to scan")
    group.add_argument("--file", help="File containing list of GitHub repository URLs")
    parser.add_argument("--output", help="File to write results to")
    args = parser.parse_args()

    repos = []
    if args.repo:
        repos.append(args.repo)
    if args.file:
        try:
            with open(args.file, 'r') as f:
                repos = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"Failed to read file {args.file}: {e}")
            sys.exit(1)

    scanner = Scanner()
    logger.info(f"Scanning {len(repos)} repositories in a single sandbox...")
    scan_result = scanner.scan_all(repos)

    for repo, found_files in scan_result.results.items():
        if found_files:
            print(f"\n[+] Found Swagger/OpenAPI files in {repo}:")
            for file_path in found_files:
                print(f"  - {file_path}")
        else:
            print(f"\n[-] No Swagger/OpenAPI files found in {repo}.")

    if args.output:
        try:
            with open(args.output, 'w') as f:
                for repo, files in scan_result.results.items():
                    f.write(f"Repository: {repo}\n")
                    for file_path in files:
                        f.write(f"- {file_path}\n")
                    f.write("\n")
            logger.info(f"Results written to {args.output}")
        except Exception as e:
            logger.error(f"Failed to write output file: {e}")

if __name__ == "__main__":
    main()

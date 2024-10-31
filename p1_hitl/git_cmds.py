import subprocess
from pathlib import Path


class GitWrapper:
    def __init__(self, repo_path) -> None:
        self.repo_path = Path(repo_path)

    def fetch(self, remote='origin'):
        result = subprocess.run(
            ['git', 'fetch', remote], cwd=self.repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # This can fail if:
        # 1. self.repo_path isn't in a git repo
        # 2. The remote is invalid
        # 3. The remote is inaccessible (no internet)
        # 4. Invalid credentials for the remote
        if result.returncode != 0:
            raise RuntimeError(
                f'Git fetch failed.\n{result.args}:\n{result.stderr}')

    def describe(self, match=None, commit=None, always=False) -> str:
        cmd_args = ['git', 'describe', '--tags']
        if match is not None:
            cmd_args.append(f'--match={match}')
        if always:
            cmd_args.append('--always')
        if commit is not None:
            cmd_args.append(commit)
        else:
            cmd_args.append('--dirty')

        result = subprocess.run(
            cmd_args, cwd=self.repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # This can fail if:
        # 1. self.repo_path isn't in a git repo
        # 2. There's no --match matches
        # 3. The commit isn't valid
        if result.returncode != 0:
            raise RuntimeError(
                f'Git describe failed.\n{result.args}:\n{result.stderr}')
        else:
            return result.stdout.strip()


def _main():
    git = GitWrapper('..')
    print(git.describe(match='lg69t-am-v?.*', commit='origin/st-develop'))


if __name__ == '__main__':
    _main()

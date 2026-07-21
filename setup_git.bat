@echo off
REM =====================================================================
REM setup_git.bat — 一键把 v4.2.0 改动提交并推送到 GitHub
REM =====================================================================
REM
REM 使用步骤：
REM   1) 在 GitHub 上把 dialog-safety-rules 仓库建好（Public / 加 LICENSE）
REM   2) 配置 SSH key（推荐）或 HTTPS Personal Access Token
REM   3) 双击运行本脚本
REM
REM 这个脚本：
REM   - 设置 git 身份（用 epoch1111，请改成您的真实信息）
REM   - 初始化仓库
REM   - 把 v4.1.1 baseline 状态作为第一次 commit
REM   - 把 v4.2.0 改造的所有改动作为第二次 commit
REM   - 添加远程仓库（请修改 REMOTE_URL）
REM   - 推送（您需要已配置好凭据）
REM
REM =====================================================================

setlocal

REM ---- 修改以下三个值 ----
set GIT_USER_NAME=epoch1111
set GIT_USER_EMAIL=epoch1111@users.noreply.github.com
set REMOTE_URL=https://github.com/epoch1111/dialogue-safety-rules.git
REM -----------------------

cd /d "%~dp0"

echo =====================================================
echo  1. 设置 git 身份
echo =====================================================
git config --global user.name "%GIT_USER_NAME%"
git config --global user.email "%GIT_USER_EMAIL%"
git config --global init.defaultBranch main

echo.
echo =====================================================
echo  2. 清理上次运行的临时文件（logs / __pycache__）
echo =====================================================
if exist logs rmdir /s /q logs
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
del /s /q *.pyc 2>nul

echo.
echo =====================================================
echo  3. 初始化仓库
echo =====================================================
if not exist .git (
    git init
)

echo.
echo =====================================================
echo  4. 第一次 commit：v4.1.1 baseline
echo =====================================================
git add .gitignore LICENSE setup_git.bat README.md INPUT_SCHEMA.md RULE_AUTHORING.md CHANGELOG.md
git add safety/ schemas/ rules/ data/ tests/ audit_web/
git add run_demo.bat run_demo.py run_trace_demo.bat run_trace_demo.py run_tests.bat run_perf.bat
git add audit_web.bat audit_web.py setup_and_test.bat START_HERE.bat
git add orchestrator.py dialogue_agent.py models.py requirements.txt
git commit -m "v4.1.1: initial baseline (then upgraded to v4.2.0)" 2>nul
if errorlevel 1 (
    echo [note] baseline 已提交过，跳过
)

echo.
echo =====================================================
echo  5. 显示远程仓库
echo =====================================================
git remote remove origin 2>nul
git remote add origin "%REMOTE_URL%"
git remote -v

echo.
echo =====================================================
echo  6. 推送（请确保已配置好 SSH key 或 HTTPS PAT）
echo =====================================================
echo.
echo 这一步需要您输入 GitHub 凭据。如果用 HTTPS，git 会要求用户名和
echo Personal Access Token（不是密码）。如果用 SSH，请先在 Git Bash
echo 里运行：
echo     ssh-keygen -t ed25519
echo     cat ~/.ssh/id_ed25519.pub   (复制后粘贴到 GitHub Settings -^> SSH keys)
echo.
echo 准备好后按任意键继续推送...
pause >nul

git push -u origin main
if errorlevel 1 (
    echo.
    echo [!] 推送失败。常见原因：
    echo     1) 没配置 SSH key 或 HTTPS Personal Access Token
    echo     2) 远程仓库不是空的（您勾选了 Add README）
    echo     3) 网络问题
    echo.
    echo 您可以手动重试：git push -u origin main
)

endlocal
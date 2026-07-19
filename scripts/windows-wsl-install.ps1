<#
.SYNOPSIS
    Windows 側で1度だけ実行する WSL2 + Ubuntu セットアップ。
    （このスクリプトの後、Ubuntu 内で scripts/setup-wsl.sh を実行する）

.DESCRIPTION
    WSL2 と Ubuntu を有効化・インストールする。初回は Windows の機能有効化のため
    再起動が必要になることがある。再起動後にもう一度このスクリプト、または
    `wsl` を起動すれば Ubuntu の初期設定（ユーザー名・パスワード）に進む。

.NOTES
    ★ 管理者権限の PowerShell で実行すること。
    実行方法:
        1) スタートメニューで「PowerShell」を右クリック →「管理者として実行」
        2) 次を実行して一時的に実行を許可:
             Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
        3) このスクリプトを実行:
             .\scripts\windows-wsl-install.ps1
#>

[CmdletBinding()]
param(
    # インストールするディストリビューション名。
    [string]$Distro = "Ubuntu"
)

$ErrorActionPreference = "Stop"

function Test-Admin {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object System.Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host "★ 管理者権限で実行してください（PowerShell を『管理者として実行』）。" -ForegroundColor Red
    exit 1
}

Write-Host "==> WSL の状態を確認しています..." -ForegroundColor Cyan

# wsl.exe が存在するか
$wslPresent = $null -ne (Get-Command wsl.exe -ErrorAction SilentlyContinue)

if (-not $wslPresent) {
    Write-Host "==> WSL が未導入のため、WSL2 + $Distro をインストールします。" -ForegroundColor Cyan
    Write-Host "    （完了後、Windows の再起動が必要な場合があります）" -ForegroundColor Yellow
    wsl --install -d $Distro
    Write-Host ""
    Write-Host "==> インストールを開始しました。" -ForegroundColor Green
    Write-Host "    再起動を求められたら再起動し、その後 Ubuntu が自動起動して" -ForegroundColor Green
    Write-Host "    ユーザー名とパスワードの設定に進みます。" -ForegroundColor Green
    exit 0
}

Write-Host "==> WSL は導入済みです。既定バージョンを WSL2 に設定します。" -ForegroundColor Cyan
wsl --set-default-version 2 | Out-Null

# 指定ディストリが入っているか（-l の出力は UTF-16 のことがあるため緩く判定）
$installed = (wsl -l -q) -replace "`0", "" | ForEach-Object { $_.Trim() }
if ($installed -notcontains $Distro) {
    Write-Host "==> $Distro が未導入のためインストールします。" -ForegroundColor Cyan
    wsl --install -d $Distro
} else {
    Write-Host "==> $Distro は導入済みです。" -ForegroundColor Green
}

Write-Host ""
Write-Host "次の手順:" -ForegroundColor Cyan
Write-Host "  1) スタートメニューから『$Distro』を起動（初回はユーザー作成）" -ForegroundColor White
Write-Host "  2) Ubuntu のシェルで、このリポジトリの scripts/setup-wsl.sh を実行:" -ForegroundColor White
Write-Host "       bash setup-wsl.sh" -ForegroundColor White
Write-Host "     （リポジトリ未取得なら setup-wsl.sh が自動 clone します）" -ForegroundColor DarkGray

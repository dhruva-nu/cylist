<#
.SYNOPSIS
Let this machine's agents use a Cylist board, from nothing, in one command.

.DESCRIPTION
The Windows half of scripts/install.sh: installs uv if it is missing,
installs the `cylist` CLI and the `cylist-mcp` server from the repository,
and hands over to `cylist setup`, which does the part that needs a password.

Nothing here needs an administrator. uv, the CLI and the MCP server all go
under the user's profile, which is also what makes the token file private —
see cylist_cli.config.describe_protection.

.PARAMETER Url
The Cylist server to set this machine up against. Without it, `cylist setup`
looks for http://localhost:8000.

.PARAMETER Ref
Branch or tag to install from. Default main.

.PARAMETER Repo
Install from a fork rather than the upstream repository.

.PARAMETER SetupArgs
Passed through to `cylist setup` (see `cylist setup --help`).

.EXAMPLE
PS> & ([scriptblock]::Create((irm https://raw.githubusercontent.com/dhruva-nu/cylist/main/scripts/install.ps1))) -Url https://cylist.example.ts.net

The invocation looks like that rather than `irm ... | iex` because `iex` on a
piped string has nowhere to put arguments. Downloaded to a file, it is the
ordinary `.\install.ps1 -Url ...`.
#>
[CmdletBinding()]
param(
    [string] $Url = '',
    [string] $Ref = 'main',
    [string] $Repo = 'https://github.com/dhruva-nu/cylist.git',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $SetupArgs = @()
)

# Stop on the first failure. Without this a PowerShell script carries on past
# a failed command and reports success at the end, which for an installer is
# the worst of both.
$ErrorActionPreference = 'Stop'

function Say([string] $Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Use-UserBin {
    # uv installs executables here on Windows, and a session started before
    # uv was will not have it. Added to this process only; uv puts it on the
    # user's PATH itself, for the sessions that come after.
    $bin = Join-Path $env:USERPROFILE '.local\bin'
    if ($env:Path -notlike "*$bin*") { $env:Path = "$bin;$env:Path" }
}

# --- uv ---------------------------------------------------------------------

Use-UserBin
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say 'Installing uv (astral.sh), into your user profile — no administrator needed.'
    # TLS 1.2 explicitly: Windows PowerShell 5.1 still defaults to SSL3/TLS1
    # on some builds, and astral.sh refuses those with an error that names
    # the cipher rather than the cause.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    Use-UserBin
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv installed but is not on PATH. Open a new terminal and run this again.'
    }
}

# --- The CLI and the MCP server --------------------------------------------

# Both from the same ref, so a machine bootstrapped from one branch does not
# end up with an MCP server from another. --force because this script is safe
# to run again, and a half-finished earlier attempt must not be what stops it.
Say "Installing cylist and cylist-mcp from $Repo@$Ref."
uv tool install --force "git+$Repo@$Ref#subdirectory=cli"
if ($LASTEXITCODE -ne 0) { throw "uv tool install (cli) failed with exit $LASTEXITCODE." }
uv tool install --force "git+$Repo@$Ref#subdirectory=mcp"
if ($LASTEXITCODE -ne 0) { throw "uv tool install (mcp) failed with exit $LASTEXITCODE." }

Use-UserBin
if (-not (Get-Command cylist -ErrorAction SilentlyContinue)) {
    throw 'cylist installed but is not on PATH. Open a new terminal and run this again.'
}

# Read by `cylist setup` if it ever has to install the MCP server itself — on
# a later run, after an upgrade removed it. Same ref as everything else.
$env:CYLIST_MCP_SOURCE = "git+$Repo@$Ref#subdirectory=mcp"

# --- Setting up -------------------------------------------------------------

Say 'Running cylist setup.'
$arguments = @()
if ($Url) { $arguments += @('--url', $Url) }
$arguments += 'setup'
$arguments += $SetupArgs

& cylist @arguments
exit $LASTEXITCODE

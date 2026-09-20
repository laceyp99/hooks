Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$ManagedScripts = @(
    "pre_tool_security.py",
    "pre_tool_dangerous_commands.py",
    "post_tool_cleaner.py",
    "session_stop.py"
)

function Ask-YesNo {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Question,

        [bool] $DefaultYes = $true
    )

    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Question $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $DefaultYes
    }

    return $answer.Trim().ToLowerInvariant().StartsWith("y")
}

function Read-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Value,

        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    # Claude Code and Codex parse these files as JSON. Write UTF-8 without a byte order mark;
    # Windows PowerShell's -Encoding utf8 would prepend one.
    $json = $Value | ConvertTo-Json -Depth 20
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $encoding)
}

function Backup-File {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $backupPath = "$Path.bak-$Timestamp"
    Copy-Item -LiteralPath $Path -Destination $backupPath
    Write-Host "Backed up $Path to $backupPath"
}

function Test-HookContainsScript {
    param(
        [AllowNull()]
        [object] $Hook,

        [Parameter(Mandatory = $true)]
        [string] $ScriptName
    )

    if ($null -eq $Hook) {
        return $false
    }

    $json = $Hook | ConvertTo-Json -Depth 20 -Compress
    return $json.Contains($ScriptName)
}

# Returns an object's property names as an array. Set-StrictMode -Version Latest makes member
# enumeration (`$o.PSObject.Properties.Name`) throw when the property collection is empty, which
# is exactly the case for a settings file that has no `hooks` key yet. Enumerating each property
# individually avoids that.
function Get-PropertyNames {
    param(
        [AllowNull()]
        [object] $Object
    )

    if ($null -eq $Object) {
        return @()
    }

    return @($Object.PSObject.Properties | ForEach-Object { $_.Name })
}

function Ensure-Property {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Object,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [object] $Value
    )

    if (-not ((Get-PropertyNames -Object $Object) -contains $Name)) {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

# Returns the existing container whose matcher equals the template's, so merged hooks keep the
# tool coverage the template declares. A template with a matcher never merges into a container
# with a different matcher; a template without a matcher only reuses a matcher-less container.
function Find-ContainerForTemplate {
    param(
        [object[]] $ExistingContainers,
        [object] $TemplateContainer
    )

    $templateHasMatcher = (Get-PropertyNames -Object $TemplateContainer) -contains "matcher"
    foreach ($container in $ExistingContainers) {
        if (-not ((Get-PropertyNames -Object $container) -contains "hooks")) {
            continue
        }

        $containerHasMatcher = (Get-PropertyNames -Object $container) -contains "matcher"
        if ($templateHasMatcher -and $containerHasMatcher -and $container.matcher -eq $TemplateContainer.matcher) {
            return $container
        }

        if (-not $templateHasMatcher -and -not $containerHasMatcher) {
            return $container
        }
    }

    return $null
}

function Get-CodexContainerHooks {
    param(
        [AllowNull()]
        [object] $Container
    )

    if ($null -eq $Container -or -not ((Get-PropertyNames -Object $Container) -contains "hooks")) {
        return @()
    }

    return @($Container.hooks)
}

# Merges hook containers shaped like { "hooks": { "<Event>": [ { "matcher": ..., "hooks": [...] } ] } }.
# Both Codex (%USERPROFILE%\.codex\hooks.json) and Claude Code (%USERPROFILE%\.claude\settings.json)
# use this layout.
function Merge-ContainerConfig {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Existing,

        [Parameter(Mandatory = $true)]
        [object] $Template,

        [string] $Name = "hook"
    )

    $changed = $false
    Ensure-Property -Object $Existing -Name "hooks" -Value ([pscustomobject]@{})

    foreach ($eventName in (Get-PropertyNames -Object $Template.hooks)) {
        $templateContainers = @($Template.hooks.$eventName)
        if (-not ((Get-PropertyNames -Object $Existing.hooks) -contains $eventName)) {
            $Existing.hooks | Add-Member -NotePropertyName $eventName -NotePropertyValue @()
        }

        $existingContainers = @($Existing.hooks.$eventName)
        foreach ($templateContainer in $templateContainers) {
            $missingHooks = @()
            foreach ($templateHook in @($templateContainer.hooks)) {
                $scriptName = $ManagedScripts | Where-Object { Test-HookContainsScript -Hook $templateHook -ScriptName $_ } | Select-Object -First 1
                if (-not $scriptName) {
                    continue
                }

                $alreadyInstalled = $existingContainers |
                    ForEach-Object { Get-CodexContainerHooks -Container $_ } |
                    Where-Object { Test-HookContainsScript -Hook $_ -ScriptName $scriptName } |
                    Select-Object -First 1
                if ($alreadyInstalled) {
                    continue
                }

                $missingHooks += $templateHook
                Write-Host "Added $Name $eventName hook for $scriptName"
            }

            if ($missingHooks.Count -eq 0) {
                continue
            }

            $targetContainer = Find-ContainerForTemplate -ExistingContainers $existingContainers -TemplateContainer $templateContainer
            if ($targetContainer) {
                $targetHooks = @($targetContainer.hooks)
                $targetContainer.hooks = @($targetHooks + $missingHooks)
            } else {
                $newContainer = $templateContainer.PSObject.Copy()
                $newContainer.hooks = $missingHooks
                $existingContainers += $newContainer
            }
            $changed = $true
        }

        $Existing.hooks.$eventName = $existingContainers
    }

    return $changed
}

function Install-Config {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $TemplatePath,

        [Parameter(Mandatory = $true)]
        [string] $DestinationPath,

        [Parameter(Mandatory = $true)]
        [scriptblock] $Merge
    )

    New-Item -ItemType Directory -Force (Split-Path -Parent $DestinationPath) | Out-Null

    if (-not (Test-Path -LiteralPath $DestinationPath)) {
        Copy-Item -LiteralPath $TemplatePath -Destination $DestinationPath
        Write-Host "Created $Name config at $DestinationPath"
        return
    }

    if (-not (Ask-YesNo "Merge missing Agent Hooks entries into existing $Name config?")) {
        Write-Host "Skipped $Name config merge."
        return
    }

    $existing = Read-JsonFile -Path $DestinationPath
    $template = Read-JsonFile -Path $TemplatePath
    $changed = & $Merge $existing $template $Name

    if (-not $changed) {
        Write-Host "$Name config already has the Agent Hooks entries."
        return
    }

    Backup-File -Path $DestinationPath
    Write-JsonFile -Value $existing -Path $DestinationPath
    Write-Host "Merged $Name config at $DestinationPath"
}

function Copy-ManagedBundle {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $SourceHooksDir,

        [Parameter(Mandatory = $true)]
        [string] $DestinationHooksDir
    )

    if (-not (Ask-YesNo "Refresh managed $Name runtime files?")) {
        Write-Host "Skipped $Name runtime files."
        return
    }

    New-Item -ItemType Directory -Force $DestinationHooksDir | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $DestinationHooksDir "scripts") | Out-Null
    Copy-Item -Force (Join-Path $SourceHooksDir "run_hook.py") (Join-Path $DestinationHooksDir "run_hook.py")
    Copy-Item -Recurse -Force (Join-Path $SourceHooksDir "scripts\*") (Join-Path $DestinationHooksDir "scripts")
    Copy-Item -Recurse -Force (Join-Path $RepoRoot "src") $env:USERPROFILE
    Write-Host "Refreshed managed $Name runtime files."
}

function Install-PiBridge {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourcePath,

        [Parameter(Mandatory = $true)]
        [string] $DestinationPath
    )

    if (-not (Ask-YesNo "Refresh managed Pi bridge extension?")) {
        Write-Host "Skipped Pi bridge extension."
        return
    }

    New-Item -ItemType Directory -Force (Split-Path -Parent $DestinationPath) | Out-Null
    if (-not (Test-Path -LiteralPath $DestinationPath)) {
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
        Write-Host "Installed Pi bridge extension at $DestinationPath"
        return
    }

    $sourceHash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash
    if ($sourceHash -eq $destinationHash) {
        Write-Host "Pi bridge at $DestinationPath is already up to date."
        return
    }

    # The bridge is a managed runtime file. Back up the existing copy so local edits are not
    # lost, then replace it with the checked-in version.
    Backup-File -Path $DestinationPath
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    Write-Host "Refreshed Pi bridge extension at $DestinationPath"
}

$claudeHooksDir = Join-Path $env:USERPROFILE ".claude\hooks"
$codexHooksDir = Join-Path $env:USERPROFILE ".codex\hooks"
$piExtensionPath = Join-Path $env:USERPROFILE ".pi\agent\extensions\agent-hooks.ts"

# Claude Code reads hooks from its user settings file. Only the "hooks" key is managed here;
# every other setting in an existing settings.json is preserved.
Install-Config `
    -Name "Claude Code" `
    -TemplatePath (Join-Path $RepoRoot ".claude\settings.example.json") `
    -DestinationPath (Join-Path $env:USERPROFILE ".claude\settings.json") `
    -Merge ${function:Merge-ContainerConfig}

Copy-ManagedBundle `
    -Name "Claude Code" `
    -SourceHooksDir (Join-Path $RepoRoot ".claude\hooks") `
    -DestinationHooksDir $claudeHooksDir

Install-Config `
    -Name "Codex" `
    -TemplatePath (Join-Path $RepoRoot ".codex\hooks.example.json") `
    -DestinationPath (Join-Path $env:USERPROFILE ".codex\hooks.json") `
    -Merge ${function:Merge-ContainerConfig}

Copy-ManagedBundle `
    -Name "Codex" `
    -SourceHooksDir (Join-Path $RepoRoot ".codex\hooks") `
    -DestinationHooksDir $codexHooksDir

Install-PiBridge `
    -SourcePath (Join-Path $RepoRoot ".pi\agent\extensions\agent-hooks.ts") `
    -DestinationPath $piExtensionPath

Write-Host "Install complete."

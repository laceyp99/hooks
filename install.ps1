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

    $Value | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $Path -Encoding utf8
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

function Ensure-Property {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Object,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [object] $Value
    )

    if (-not ($Object.PSObject.Properties.Name -contains $Name)) {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Merge-CopilotConfig {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Existing,

        [Parameter(Mandatory = $true)]
        [object] $Template
    )

    $changed = $false
    Ensure-Property -Object $Existing -Name "version" -Value $Template.version
    Ensure-Property -Object $Existing -Name "hooks" -Value ([pscustomobject]@{})

    foreach ($eventName in $Template.hooks.PSObject.Properties.Name) {
        $templateHooks = @($Template.hooks.$eventName)
        if (-not ($Existing.hooks.PSObject.Properties.Name -contains $eventName)) {
            $Existing.hooks | Add-Member -NotePropertyName $eventName -NotePropertyValue @()
        }

        $existingHooks = @($Existing.hooks.$eventName)
        foreach ($templateHook in $templateHooks) {
            $scriptName = $ManagedScripts | Where-Object { Test-HookContainsScript -Hook $templateHook -ScriptName $_ } | Select-Object -First 1
            if (-not $scriptName) {
                continue
            }

            $alreadyInstalled = $existingHooks | Where-Object { Test-HookContainsScript -Hook $_ -ScriptName $scriptName } | Select-Object -First 1
            if ($alreadyInstalled) {
                continue
            }

            $existingHooks += $templateHook
            $changed = $true
            Write-Host "Added Copilot $eventName hook for $scriptName"
        }

        $Existing.hooks.$eventName = $existingHooks
    }

    return $changed
}

function Find-CodexContainerForTemplate {
    param(
        [object[]] $ExistingContainers,
        [object] $TemplateContainer
    )

    if ($TemplateContainer.PSObject.Properties.Name -contains "matcher") {
        $matching = $ExistingContainers |
            Where-Object { ($_.PSObject.Properties.Name -contains "matcher") -and $_.matcher -eq $TemplateContainer.matcher } |
            Select-Object -First 1
        if ($matching) {
            return $matching
        }
    }

    return $ExistingContainers |
        Where-Object { $_.PSObject.Properties.Name -contains "hooks" } |
        Select-Object -First 1
}

function Get-CodexContainerHooks {
    param(
        [AllowNull()]
        [object] $Container
    )

    if ($null -eq $Container -or -not ($Container.PSObject.Properties.Name -contains "hooks")) {
        return @()
    }

    return @($Container.hooks)
}

function Merge-CodexConfig {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Existing,

        [Parameter(Mandatory = $true)]
        [object] $Template
    )

    $changed = $false
    Ensure-Property -Object $Existing -Name "hooks" -Value ([pscustomobject]@{})

    foreach ($eventName in $Template.hooks.PSObject.Properties.Name) {
        $templateContainers = @($Template.hooks.$eventName)
        if (-not ($Existing.hooks.PSObject.Properties.Name -contains $eventName)) {
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
                Write-Host "Added Codex $eventName hook for $scriptName"
            }

            if ($missingHooks.Count -eq 0) {
                continue
            }

            $targetContainer = Find-CodexContainerForTemplate -ExistingContainers $existingContainers -TemplateContainer $templateContainer
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
    $changed = & $Merge $existing $template

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

    if (-not (Ask-YesNo "Install Pi bridge extension if it is missing?")) {
        Write-Host "Skipped Pi bridge extension."
        return
    }

    New-Item -ItemType Directory -Force (Split-Path -Parent $DestinationPath) | Out-Null
    if (Test-Path -LiteralPath $DestinationPath) {
        Write-Host "Pi bridge already exists at $DestinationPath; leaving it unchanged."
        return
    }

    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
    Write-Host "Installed Pi bridge extension at $DestinationPath"
}

$copilotHooksDir = Join-Path $env:USERPROFILE ".copilot\hooks"
$codexHooksDir = Join-Path $env:USERPROFILE ".codex\hooks"
$piExtensionPath = Join-Path $env:USERPROFILE ".pi\agent\extensions\agent-hooks.ts"

Install-Config `
    -Name "Copilot" `
    -TemplatePath (Join-Path $RepoRoot ".copilot\hooks\hooks.example.json") `
    -DestinationPath (Join-Path $copilotHooksDir "hooks.json") `
    -Merge ${function:Merge-CopilotConfig}

Copy-ManagedBundle `
    -Name "Copilot" `
    -SourceHooksDir (Join-Path $RepoRoot ".copilot\hooks") `
    -DestinationHooksDir $copilotHooksDir

Install-Config `
    -Name "Codex" `
    -TemplatePath (Join-Path $RepoRoot ".codex\hooks.example.json") `
    -DestinationPath (Join-Path $env:USERPROFILE ".codex\hooks.json") `
    -Merge ${function:Merge-CodexConfig}

Copy-ManagedBundle `
    -Name "Codex" `
    -SourceHooksDir (Join-Path $RepoRoot ".codex\hooks") `
    -DestinationHooksDir $codexHooksDir

Install-PiBridge `
    -SourcePath (Join-Path $RepoRoot ".pi\agent\extensions\agent-hooks.ts") `
    -DestinationPath $piExtensionPath

Write-Host "Install complete."

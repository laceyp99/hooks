Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

# Managed hook entries, keyed by the event argument they pass to run_hook.py. Each lists the
# per-script entries an install from before the single runner registered for the same job, so
# those are recognized, rewritten to the current command, and deduplicated.
$ManagedHooks = [ordered]@{
    "pre-tool"  = @("pre_tool_security.py", "pre_tool_dangerous_commands.py")
    "post-tool" = @("post_tool_cleaner.py")
    "stop"      = @("session_stop.py")
}

# Files older installs placed in each harness's hooks\scripts directory. The runner no longer
# uses them; they are removed so a stale copy of the rules cannot be run by mistake.
$LegacyBundleScripts = @(
    "pre_tool_security.py",
    "pre_tool_dangerous_commands.py",
    "post_tool_cleaner.py",
    "session_stop.py",
    "ruff_support.py"
)

# Modules older installs placed in %USERPROFILE%\src\agent_hooks that no longer exist.
$LegacyPackageModules = @(
    "bootstrap.py"
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

# Returns the managed hook id ("pre-tool", "post-tool", "stop") a hook entry runs, or $null for a
# hook this installer does not manage. Only commands that launch run_hook.py count. The current
# form passes the id as the runner's argument; the legacy form passed a script path, which maps
# to the id that replaced it.
function Get-ManagedHookId {
    param(
        [AllowNull()]
        [object] $Hook
    )

    if ($null -eq $Hook) {
        return $null
    }

    $commands = @()
    foreach ($field in @("command", "commandWindows")) {
        if ((Get-PropertyNames -Object $Hook) -contains $field -and $Hook.$field -is [string]) {
            $commands += $Hook.$field
        }
    }

    foreach ($command in $commands) {
        if (-not $command.Contains("run_hook.py")) {
            continue
        }

        foreach ($id in $ManagedHooks.Keys) {
            if ($command -match ('run_hook\.py["'']?\s+' + [regex]::Escape($id) + '(\s|$)')) {
                return $id
            }
        }

        foreach ($id in $ManagedHooks.Keys) {
            foreach ($legacyScript in $ManagedHooks[$id]) {
                if ($command.Contains($legacyScript)) {
                    return $id
                }
            }
        }
    }

    return $null
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

# Keeps the matcher on an already-installed managed container in step with the template. Without
# this a template matcher change never reaches an existing install: the managed hooks are already
# present, so nothing is added and the stale matcher silently keeps its old tool coverage.
function Sync-ContainerMatcher {
    param(
        [object[]] $ExistingContainers,
        [object] $TemplateContainer,
        [string[]] $HookIds,
        [string] $Name,
        [string] $EventName
    )

    if ($null -eq $HookIds -or $HookIds.Count -eq 0) {
        return $false
    }

    if (-not ((Get-PropertyNames -Object $TemplateContainer) -contains "matcher")) {
        return $false
    }

    $templateMatcher = $TemplateContainer.matcher
    $updated = $false
    foreach ($container in $ExistingContainers) {
        $holdsManaged = $false
        foreach ($existingHook in (Get-CodexContainerHooks -Container $container)) {
            if ($HookIds -contains (Get-ManagedHookId -Hook $existingHook)) {
                $holdsManaged = $true
                break
            }
        }

        if (-not $holdsManaged) {
            continue
        }

        if ((Get-PropertyNames -Object $container) -contains "matcher") {
            if ($container.matcher -eq $templateMatcher) {
                continue
            }

            $container.matcher = $templateMatcher
        } else {
            $container | Add-Member -NotePropertyName "matcher" -NotePropertyValue $templateMatcher
        }

        Write-Host "Updated $Name $EventName matcher to $templateMatcher"
        $updated = $true
    }

    return $updated
}

# Keeps an already-installed managed hook entry in step with the template, so a changed command,
# timeout, or status message reaches an existing install. Only the fields the template declares
# are touched.
function Sync-ManagedHookEntry {
    param(
        [object] $ExistingHook,
        [object] $TemplateHook,
        [string] $HookId,
        [string] $Name,
        [string] $EventName
    )

    $syncFields = @("command", "commandWindows", "timeout", "statusMessage")
    $updated = $false

    foreach ($field in $syncFields) {
        if (-not ((Get-PropertyNames -Object $TemplateHook) -contains $field)) {
            continue
        }

        $templateValue = $TemplateHook.$field
        if ((Get-PropertyNames -Object $ExistingHook) -contains $field) {
            if ($ExistingHook.$field -eq $templateValue) {
                continue
            }

            $ExistingHook.$field = $templateValue
        } else {
            $ExistingHook | Add-Member -NotePropertyName $field -NotePropertyValue $templateValue
        }

        Write-Host "Updated $Name $EventName $field for $HookId"
        $updated = $true
    }

    return $updated
}

# Returns every hook entry across the given containers that runs the managed hook $HookId, in
# file order. An install that predates the single runner has one legacy entry per script, so the
# pre-tool id can match two entries here.
function Find-ManagedHookEntries {
    param(
        [object[]] $ExistingContainers,
        [string] $HookId
    )

    $found = @()
    foreach ($container in $ExistingContainers) {
        foreach ($existingHook in (Get-CodexContainerHooks -Container $container)) {
            if ((Get-ManagedHookId -Hook $existingHook) -eq $HookId) {
                $found += $existingHook
            }
        }
    }

    return , $found
}

# Removes the given hook entries from whichever containers hold them and returns the containers
# that this left with no hooks at all. Entries are matched by reference, never by content, so a
# user's own hook that happens to look the same is never removed.
function Remove-HookEntries {
    param(
        [object[]] $ExistingContainers,
        [object[]] $Entries
    )

    $emptied = @()
    foreach ($container in $ExistingContainers) {
        $hooks = @(Get-CodexContainerHooks -Container $container)
        if ($hooks.Count -eq 0) {
            continue
        }

        $kept = @()
        foreach ($existingHook in $hooks) {
            $isTarget = $false
            foreach ($entry in $Entries) {
                if ([object]::ReferenceEquals($existingHook, $entry)) {
                    $isTarget = $true
                    break
                }
            }

            if (-not $isTarget) {
                $kept += $existingHook
            }
        }

        if ($kept.Count -eq $hooks.Count) {
            continue
        }

        $container.hooks = $kept
        if ($kept.Count -eq 0) {
            $emptied += $container
        }
    }

    return , $emptied
}

# Merges hook containers and any permission rules declared by the template. Hook containers are
# shaped like { "hooks": { "<Event>": [ { "matcher": ..., "hooks": [...] } ] } }.
# Both Codex (%USERPROFILE%\.codex\hooks.json) and Claude Code (%USERPROFILE%\.claude\settings.json)
# use this layout.
#
# Each template hook is a managed id (pre-tool, post-tool, stop). For each one, the first existing
# entry that runs it, in its current or legacy form, is kept and brought in step with the
# template; any further entries for the same id are removed, which is how the two legacy pre-tool
# entries collapse into one. A container left empty only by that removal is dropped too. Hooks
# that do not launch run_hook.py are never touched.
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
        $emptiedContainers = @()
        foreach ($templateContainer in $templateContainers) {
            $missingHooks = @()
            $templateHookIds = @()
            foreach ($templateHook in @($templateContainer.hooks)) {
                $hookId = Get-ManagedHookId -Hook $templateHook
                if (-not $hookId) {
                    continue
                }

                $templateHookIds += $hookId

                $installed = Find-ManagedHookEntries -ExistingContainers $existingContainers -HookId $hookId
                if ($installed.Count -eq 0) {
                    $missingHooks += $templateHook
                    Write-Host "Added $Name $eventName hook for $hookId"
                    continue
                }

                if (Sync-ManagedHookEntry -ExistingHook $installed[0] -TemplateHook $templateHook -HookId $hookId -Name $Name -EventName $eventName) {
                    $changed = $true
                }

                if ($installed.Count -gt 1) {
                    $superseded = @($installed | Select-Object -Skip 1)
                    $emptiedContainers += Remove-HookEntries -ExistingContainers $existingContainers -Entries $superseded
                    Write-Host "Removed $($superseded.Count) superseded $Name $eventName hook entr$(if ($superseded.Count -eq 1) { 'y' } else { 'ies' }) for $hookId"
                    $changed = $true
                }
            }

            if (Sync-ContainerMatcher -ExistingContainers $existingContainers -TemplateContainer $templateContainer -HookIds $templateHookIds -Name $Name -EventName $eventName) {
                $changed = $true
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

        if ($emptiedContainers.Count -gt 0) {
            $existingContainers = @(
                $existingContainers | Where-Object {
                    $candidate = $_
                    $wasEmptied = $emptiedContainers | Where-Object { [object]::ReferenceEquals($_, $candidate) }
                    -not ($wasEmptied -and @(Get-CodexContainerHooks -Container $candidate).Count -eq 0)
                }
            )
        }

        $Existing.hooks.$eventName = $existingContainers
    }

    if ((Get-PropertyNames -Object $Template) -contains "permissions") {
        if (-not ((Get-PropertyNames -Object $Existing) -contains "permissions")) {
            $Existing | Add-Member -NotePropertyName "permissions" -NotePropertyValue ([pscustomobject]@{})
        } elseif ($null -eq $Existing.permissions) {
            $Existing.permissions = [pscustomobject]@{}
        }

        foreach ($permissionName in (Get-PropertyNames -Object $Template.permissions)) {
            if (-not ((Get-PropertyNames -Object $Existing.permissions) -contains $permissionName)) {
                $Existing.permissions | Add-Member -NotePropertyName $permissionName -NotePropertyValue @()
            }

            $existingRules = @($Existing.permissions.$permissionName)
            foreach ($rule in @($Template.permissions.$permissionName)) {
                if ($existingRules -contains $rule) {
                    continue
                }

                $existingRules += $rule
                $changed = $true
                Write-Host "Added $Name permission deny rule $rule"
            }
            $Existing.permissions.$permissionName = $existingRules
        }
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
        [scriptblock] $Merge,

        # Printed whenever this config is created or changed. Used to tell Codex users that a
        # changed hook command invalidates the trust hash Codex keeps in config.toml.
        [string] $WriteNote = ""
    )

    New-Item -ItemType Directory -Force (Split-Path -Parent $DestinationPath) | Out-Null

    if (-not (Test-Path -LiteralPath $DestinationPath)) {
        Copy-Item -LiteralPath $TemplatePath -Destination $DestinationPath
        Write-Host "Created $Name config at $DestinationPath"
        if ($WriteNote) {
            Write-Host $WriteNote
        }

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
    if ($WriteNote) {
        Write-Host $WriteNote
    }
}

# Removes files an older install managed but the current layout no longer uses. Only the named
# files are deleted, plus Python's bytecode cache beside them; a directory is removed only once
# that leaves it empty, so anything a user put there stays, with a note saying so.
function Remove-StaleManagedFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Directory,

        [Parameter(Mandatory = $true)]
        [string[]] $FileNames,

        [switch] $RemoveDirectoryWhenEmpty
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return
    }

    foreach ($fileName in $FileNames) {
        $path = Join-Path $Directory $fileName
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            Remove-Item -LiteralPath $path -Force
            Write-Host "Removed stale managed file $path"
        }

        $stem = [System.IO.Path]::GetFileNameWithoutExtension($fileName)
        $cacheDir = Join-Path $Directory "__pycache__"
        if (Test-Path -LiteralPath $cacheDir -PathType Container) {
            Get-ChildItem -LiteralPath $cacheDir -Filter "$stem.*.pyc" -File | Remove-Item -Force
        }
    }

    if (-not $RemoveDirectoryWhenEmpty) {
        return
    }

    $cacheDir = Join-Path $Directory "__pycache__"
    if ((Test-Path -LiteralPath $cacheDir -PathType Container) -and -not (Get-ChildItem -LiteralPath $cacheDir -Force)) {
        Remove-Item -LiteralPath $cacheDir -Force
    }

    if (Get-ChildItem -LiteralPath $Directory -Force) {
        Write-Host "Left $Directory in place: it still holds files this installer did not put there."
        return
    }

    Remove-Item -LiteralPath $Directory -Force
    Write-Host "Removed stale managed directory $Directory"
}

function Copy-ManagedBundle {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $DestinationHooksDir
    )

    if (-not (Ask-YesNo "Refresh managed $Name runtime files?")) {
        Write-Host "Skipped $Name runtime files."
        return
    }

    # One runner serves every harness. It finds the shared logic at %USERPROFILE%\src\agent_hooks,
    # two levels above %USERPROFILE%\.<harness>\hooks\run_hook.py.
    New-Item -ItemType Directory -Force $DestinationHooksDir | Out-Null
    Copy-Item -Force (Join-Path $RepoRoot "bin\run_hook.py") (Join-Path $DestinationHooksDir "run_hook.py")
    Copy-Item -Recurse -Force (Join-Path $RepoRoot "src") $env:USERPROFILE

    # Earlier installs copied per-script wrappers into hooks\scripts and a bootstrap module into
    # the shared package. Nothing runs them any more.
    Remove-StaleManagedFiles -Directory (Join-Path $DestinationHooksDir "scripts") -FileNames $LegacyBundleScripts -RemoveDirectoryWhenEmpty
    Remove-StaleManagedFiles -Directory (Join-Path $env:USERPROFILE "src\agent_hooks") -FileNames $LegacyPackageModules
    Write-Host "Refreshed managed $Name runtime files."
}

# Installs a single managed runtime file, such as a harness bridge written in TypeScript. Pi and
# OpenCode both register their hooks by dropping one file into a directory the harness scans, so
# neither needs the JSON merge the Claude Code and Codex configs go through.
function Install-ManagedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $SourcePath,

        [Parameter(Mandatory = $true)]
        [string] $DestinationPath
    )

    if (-not (Ask-YesNo "Refresh managed ${Name}?")) {
        Write-Host "Skipped $Name."
        return
    }

    New-Item -ItemType Directory -Force (Split-Path -Parent $DestinationPath) | Out-Null
    if (-not (Test-Path -LiteralPath $DestinationPath)) {
        Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath
        Write-Host "Installed $Name at $DestinationPath"
        return
    }

    $sourceHash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash
    if ($sourceHash -eq $destinationHash) {
        Write-Host "$Name at $DestinationPath is already up to date."
        return
    }

    # The bridge is a managed runtime file. Back up the existing copy so local edits are not
    # lost, then replace it with the checked-in version.
    Backup-File -Path $DestinationPath
    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
    Write-Host "Refreshed $Name at $DestinationPath"
}

$claudeHooksDir = Join-Path $env:USERPROFILE ".claude\hooks"
$codexHooksDir = Join-Path $env:USERPROFILE ".codex\hooks"
$piExtensionPath = Join-Path $env:USERPROFILE ".pi\agent\extensions\agent-hooks.ts"
# OpenCode finds its global config directory through the xdg-basedir package: XDG_CONFIG_HOME
# when it is set and non-empty, otherwise ~/.config, on every platform including Windows. Resolve
# it the same way, or a user with XDG_CONFIG_HOME set gets the plugin written somewhere OpenCode
# never looks, and the hooks silently never run.
$openCodeConfigRoot = if ([string]::IsNullOrEmpty($env:XDG_CONFIG_HOME)) {
    Join-Path $env:USERPROFILE ".config"
} else {
    $env:XDG_CONFIG_HOME
}
$openCodePluginPath = Join-Path $openCodeConfigRoot "opencode\plugins\agent-hooks.ts"

# Claude Code reads hooks and permission rules from its user settings file. The installer merges
# the protected-path deny rules while preserving the user's existing permissions and settings.
Install-Config `
    -Name "Claude Code" `
    -TemplatePath (Join-Path $RepoRoot ".claude\settings.example.json") `
    -DestinationPath (Join-Path $env:USERPROFILE ".claude\settings.json") `
    -Merge ${function:Merge-ContainerConfig}

Copy-ManagedBundle `
    -Name "Claude Code" `
    -DestinationHooksDir $claudeHooksDir

# Codex keeps a trusted_hash per hook under [hooks.state] in config.toml. Any change to a hook
# command invalidates it, and an untrusted hook is silently skipped: the session runs with no
# hook output and nothing says a guard was bypassed. Say so whenever this file is written.
Install-Config `
    -Name "Codex" `
    -TemplatePath (Join-Path $RepoRoot ".codex\hooks.example.json") `
    -DestinationPath (Join-Path $env:USERPROFILE ".codex\hooks.json") `
    -Merge ${function:Merge-ContainerConfig} `
    -WriteNote "  Note: Codex will treat these hooks as new or modified and will not run them until you review and trust them in the Codex TUI."

Copy-ManagedBundle `
    -Name "Codex" `
    -DestinationHooksDir $codexHooksDir

Install-ManagedFile `
    -Name "Pi bridge extension" `
    -SourcePath (Join-Path $RepoRoot ".pi\agent\extensions\agent-hooks.ts") `
    -DestinationPath $piExtensionPath

# OpenCode auto-loads every file in its plugin directory, so dropping the bridge there is the
# whole registration step; there is no config file to merge. Like the Pi bridge, the plugin only
# shells out to the Python in this checkout, so the checkout has to stay where it is.
#
# The checked-in copy lives outside .opencode/plugins/ on purpose. OpenCode also scans that
# directory in whatever project it runs in, this one included, and it dedupes by file path, so a
# copy there would load a second time alongside the installed one whenever OpenCode runs here.
Install-ManagedFile `
    -Name "OpenCode plugin" `
    -SourcePath (Join-Path $RepoRoot ".opencode\agent-hooks.example.ts") `
    -DestinationPath $openCodePluginPath

Write-Host "Install complete."

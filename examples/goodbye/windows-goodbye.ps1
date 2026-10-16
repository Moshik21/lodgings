# Example: say goodbye from a Windows host before it sleeps or restarts (Windows Update restarts included).
# Not installed by Lodgings, and Lodgings never runs this file. Read it, edit the four values, then run it yourself in a
# PowerShell window as the user whose ssh configuration reaches the collector:
#
#     powershell -ExecutionPolicy Bypass -File windows-goodbye.ps1              # registers two scheduled tasks
#     powershell -ExecutionPolicy Bypass -File windows-goodbye.ps1 -Remove      # removes them again
#
# It registers two tasks under \Lodgings\ that run only while you are logged on:
#   LodgingsGoodbyeSleep    on System event 42 (Kernel-Power: the system is entering sleep)  -> --goodbye sleep
#   LodgingsGoodbyeRestart  on System event 1074 (User32: a restart or shutdown was started,
#                         including by Windows Update)                                    -> --goodbye reboot
# Windows gives a sleep trigger very little time; a goodbye that does not arrive only means the host reads LOST until
# it beats again. A restart is slower and usually gets its goodbye out.
param([switch]$Remove)

$Lodgings  = "C:\lodgings\bin\lodgings"          # this host's copy of Lodgings (the Python entry point, not lodgings.cmd)
$HostName = "host-a-win"                    # this host's name in hosts.toml
$Python  = "py.exe"                        # or the full path to python.exe
$Deliver = @("ssh", "-o", "ConnectTimeout=3", "collector", "python3", "lodgings/bin/lodgings", "record", "-")

$Folder = "\Lodgings\"
$Tasks = @{
    "LodgingsGoodbyeSleep"   = @{ Reason = "sleep";  Query = "*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=42]]" }
    "LodgingsGoodbyeRestart" = @{ Reason = "reboot"; Query = "*[System[Provider[@Name='User32'] and EventID=1074]]" }
}

if ($Remove) {
    foreach ($name in $Tasks.Keys) {
        Unregister-ScheduledTask -TaskPath $Folder -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Output "removed the Lodgings goodbye tasks (if they existed)"
    return
}

$class = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
foreach ($name in $Tasks.Keys) {
    $t = $Tasks[$name]
    $trigger = New-CimInstance -CimClass $class -ClientOnly
    $trigger.Enabled = $true
    $trigger.Subscription = "<QueryList><Query Id='0' Path='System'><Select Path='System'>$($t.Query)</Select></Query></QueryList>"
    $argv = @("-3", $Lodgings, "beat", "--host", $HostName, "--goodbye", $t.Reason, "--") + $Deliver
    $quoted = ($argv | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
    $action = New-ScheduledTaskAction -Execute $Python -Argument $quoted
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds 30)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
    Register-ScheduledTask -TaskPath $Folder -TaskName $name -Trigger $trigger -Action $action -Settings $settings -Principal $principal -Force | Out-Null
    Write-Output "registered $Folder$name ($($t.Reason))"
}

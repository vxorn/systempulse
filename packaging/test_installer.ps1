$ErrorActionPreference = 'Stop'
$install = Join-Path $env:RUNNER_TEMP 'SystemPulse Install Test'
$log = Join-Path $env:RUNNER_TEMP 'systempulse-setup.log'
$arguments = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=`"$install`" /LOG=`"$log`""
$setup = Start-Process 'installer/SystemPulse-Setup-Windows-x64.exe' -ArgumentList $arguments -Wait -PassThru
if ($setup.ExitCode -ne 0) {
    Get-Content $log
    throw "Installer returned $($setup.ExitCode)"
}

$app = Start-Process (Join-Path $install 'SystemPulse.exe') -PassThru
try {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 250
        $app.Refresh()
        if ($app.HasExited) { throw "Installed GUI exited with $($app.ExitCode)" }
    } while ($app.MainWindowTitle -ne 'SystemPulse' -and (Get-Date) -lt $deadline)
    if ($app.MainWindowTitle -ne 'SystemPulse') { throw 'SystemPulse window did not open' }
    if (-not $app.CloseMainWindow() -or -not $app.WaitForExit(10000)) {
        throw 'SystemPulse window did not close'
    }
} finally {
    if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
}

$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'SystemPulse.lnk'
if (-not (Test-Path $shortcut)) { throw 'Start menu shortcut missing' }
$uninstaller = Get-ChildItem $install -Filter 'unins*.exe' | Select-Object -First 1
$uninstall = Start-Process $uninstaller.FullName -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -Wait -PassThru
if ($uninstall.ExitCode -ne 0 -or (Test-Path (Join-Path $install 'SystemPulse.exe')) -or (Test-Path $shortcut)) {
    throw 'SystemPulse was not fully uninstalled'
}

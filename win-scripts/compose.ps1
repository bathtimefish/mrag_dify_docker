# Capture the forwarded compose arguments through the automatic $args variable.
# A declared ValueFromRemainingArguments parameter drops short flags such as
# "-d" when this script is launched with powershell.exe -File (compose.bat),
# whereas $args preserves every token verbatim.
. "$PSScriptRoot\windows-common.ps1"

[string[]]$composeArguments = $args
Invoke-ProjectCompose $composeArguments

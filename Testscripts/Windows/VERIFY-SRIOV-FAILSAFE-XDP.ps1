# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the Apache License.

<#
.Description
    This script deploys the VM, verifies xdp working with
    SRIOV-enabled nic and synthetic nic
#>

param([object] $AllVmData,
	[object] $CurrentTestData)

$iFaceName = "eth1"

# This function will start ping and xdpdump on client
function Ping-XDPDump {
    $VMData = $args[0]
    $IP = $args[1]
    $NIC = $args[2]
    $LogSuffix = $args[3]
    # Start Ping test
    $ping_command = "ping -I $NIC -c 30 $IP > ~/pingOut$LogSuffix.txt"
    Write-LogInfo "Starting command: $ping_command on $($VMData.RoleName)"
    Run-LinuxCmd -ip $VMData.PublicIP -port $VMData.SSHPort -username $user -password $password `
        -command $ping_command -RunInBackground -runAsSudo

    # Start XDPDump
    $xdp_command = "cd /root/bpf-samples/xdpdump && timeout 10 ./xdpdump -i $NIC > ~/xdpdumpout$LogSuffix.txt"
    Write-LogInfo "Starting command: $xdp_command on $($VMData.RoleName)"
    $testJob = Run-LinuxCmd -ip $VMData.PublicIP -port $VMData.SSHPort -username $user -password $password `
        -command $xdp_command -RunInBackground -runAsSudo
    $timer = 0
    while ($testJob -and ((Get-Job -Id $testJob).State -eq "Running")) {
        $currentStatus = Run-LinuxCmd -ip $VMData.PublicIP -port $VMData.SSHPort -username $user -password $password `
            -command "tail -2 ~/xdpdumpout$LogSuffix.txt | head -1" -runAsSudo
        Write-LogInfo "Current Test Status: $currentStatus"
        Wait-Time -seconds 5
        $timer += 1
        if ($timer -gt 15) {
            Throw "XDPSetup did not stop after 5 mins. Please check logs."
        }
    }

    $currentStatus = Run-LinuxCmd -ip $VMData.PublicIP -port $VMData.SSHPort -username $user -password $password `
        -command "tail -1 ~/xdpdumpout$LogSuffix.txt" -runAsSudo
    if ( ($currentStatus -inotmatch "unloading xdp") ) {
        Write-LogErr "Test Aborted. Last known status : $currentStatus."
        Throw "XDP Execution failed"
    }
    Write-LogInfo "XDPDump application ran successfully."
    return "PASS"
}

function Main {
    try{
        $noClient = $true
        $noServer = $true
        foreach ($vmData in $allVMData) {
            if ($vmData.RoleName -imatch "receiver") {
                $clientVMData = $vmData
                $noClient = $false
            }
            elseif ($vmData.RoleName -imatch "sender") {
                $noServer = $false
                $serverVMData = $vmData
            }
        }
        if ($noClient) {
            Throw "No any receiver VM defined. Aborting Test."
        }
        if ($noServer) {
            Throw "No any sender VM defined. Aborting Test."
        }

        # CONFIGURE VM Details
        Write-LogInfo "CLIENT VM details :"
        Write-LogInfo "  RoleName : $($clientVMData.RoleName)"
        Write-LogInfo "  Public IP : $($clientVMData.PublicIP)"
        Write-LogInfo "  SSH Port : $($clientVMData.SSHPort)"
        Write-LogInfo "  Internal IP : $($clientVMData.InternalIP)"
        Write-LogInfo "SERVER VM details :"
        Write-LogInfo "  RoleName : $($serverVMData.RoleName)"
        Write-LogInfo "  Public IP : $($serverVMData.PublicIP)"
        Write-LogInfo "  SSH Port : $($serverVMData.SSHPort)"
        Write-LogInfo "  Internal IP : $($serverVMData.InternalIP)"

        # PROVISION VMS
        Provision-VMsForLisa -allVMData $allVMData -installPackagesOnRoleNames "none"

        # Generate constants.sh and write all VM info into it
        Write-LogInfo "Generating constants.sh ..."
        $constantsFile = "$LogDir\constants.sh"
        Set-Content -Value "# Generated by Azure Automation." -Path $constantsFile
        Add-Content -Value "ip=$($clientVMData.InternalIP)" -Path $constantsFile
        Add-Content -Value "nicName=$iFaceName" -Path $constantsFile
        foreach ($param in $currentTestData.TestParameters.param) {
            Add-Content -Value "$param" -Path $constantsFile
        }
        Write-LogInfo "constants.sh created successfully..."
        Write-LogInfo (Get-Content -Path $constantsFile)

        # Build and Install XDP Dump application
        $installXDPCommand = @"
./XDPDumpSetup.sh 2>&1 > ~/xdpConsoleLogs.txt
. utils.sh
collect_VM_properties
"@
        Set-Content "$LogDir\StartXDPSetup.sh" $installXDPCommand
        Copy-RemoteFiles -uploadTo $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -files "$constantsFile,$LogDir\StartXDPSetup.sh" `
            -username $user -password $password -upload -runAsSudo

        Run-LinuxCmd -ip $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -username $user -password $password -command "chmod +x *.sh" -runAsSudo | Out-Null
        $testJob = Run-LinuxCmd -ip $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -username $user -password $password -command "./StartXDPSetup.sh" `
            -RunInBackground -runAsSudo
        # Terminate process if ran more than 5 mins
        # TODO: Check max installation time for other distros when added
        $timer = 0
        while ($testJob -and ((Get-Job -Id $testJob).State -eq "Running")) {
            $currentStatus = Run-LinuxCmd -ip $clientVMData.PublicIP -port $clientVMData.SSHPort `
                -username $user -password $password -command "tail -2 ~/xdpConsoleLogs.txt | head -1" -runAsSudo
            Write-LogInfo "Current Test Status: $currentStatus"
            Wait-Time -seconds 20
            $timer += 1
            if ($timer -gt 15) {
                Throw "XDPSetup did not stop after 5 mins. Please check logs."
            }
        }

        $currentStatus = Run-LinuxCmd -ip $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -username $user -password $password -command "tail -1 ~/xdpConsoleLogs.txt" -runAsSudo
        $currentState = Run-LinuxCmd -ip $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -username $user -password $password -command "cat state.txt" -runAsSudo
        if ($currentState -imatch "TestCompleted") {
            Write-LogInfo "XDPSetup successfully ran on $($clientVMDAta.RoleName)"

            # Verify xdpdump on SRIOV
            $testResult = Ping-XDPDump $clientVMData $serverVMData.SecondInternalIP "$iFaceName" "_SRIOV"

            # Disable SRIOV
            Write-LogInfo "Disabling SRIOV"
            $sriovStatus = $false
            $sriovStatus = Set-SRIOVInVMs -AllVMData $AllVMData -Disable
            $clientVMData.PublicIP = $AllVMData.PublicIP[0]
            if ($sriovStatus -eq $false) {
                Write-LogErr "Disable SRIOV failed."
                Throw "Failed to disable SRIOV"
            }
            Write-LogInfo "Disabling SRIOV Successful."

            # Verify xdpdump on synthetic
            $testResult = Ping-XDPDump $clientVMData $serverVMData.SecondInternalIP "$iFaceName" "_Synthetic"

        }   elseif ($currentState -imatch "TestAborted") {
            Write-LogErr "Test Aborted. Last known status: $currentStatus."
            $testResult = "ABORTED"
        }   elseif ($currentState -imatch "TestSkipped") {
            Write-LogErr "Test Skipped. Last known status: $currentStatus"
            $testResult = "SKIPPED"
        }	elseif ($currentState -imatch "TestFailed") {
            Write-LogErr "Test failed. Last known status: $currentStatus."
            $testResult = "FAIL"
        }	else {
            Write-LogErr "Test execution is not successful, check test logs in VM."
            $testResult = "ABORTED"
        }

        Copy-RemoteFiles -downloadFrom $clientVMData.PublicIP -port $clientVMData.SSHPort `
            -username $user -password $password -download `
            -downloadTo $LogDir -files "*.txt, *.log"
    } catch {
        $ErrorMessage =  $_.Exception.Message
        $ErrorLine = $_.InvocationInfo.ScriptLineNumber
        Write-LogErr "EXCEPTION: $ErrorMessage at line: $ErrorLine"
        $testResult = "FAIL"
    } finally {
        if (!$testResult) {
            $testResult = "ABORTED"
        }
        $resultArr += $testResult
    }
    Write-LogInfo "Test result: $testResult"
    return $testResult
}

Main

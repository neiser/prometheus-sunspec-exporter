{ config, lib, ... }:

let
  cfg = config.services.prometheus-sunspec-exporter;
in
{
  options.services.prometheus-sunspec-exporter = {
    enable = lib.mkEnableOption "the SunSpec Prometheus exporter";

    package = lib.mkOption {
      type = lib.types.package;
      description = "Exporter package to use.";
    };

    sunspecHost = lib.mkOption {
      type = lib.types.str;
      example = "192.168.178.104";
      description = "Host name or IP address of the SunSpec device (Modbus TCP).";
    };

    sunspecPort = lib.mkOption {
      type = lib.types.port;
      default = 502;
      description = "Modbus TCP port of the SunSpec device.";
    };

    sunspecAddress = lib.mkOption {
      type = lib.types.ints.between 0 255;
      default = 1;
      description = "Modbus unit id of the SunSpec device.";
    };

    modelIds = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "101" ];
      description = "SunSpec model ids to export.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 9807;
      description = "Port the HTTP metrics endpoint listens on, on all addresses.";
    };

    extraArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "--filter" "Amps_Phase[A-Z]_Aph[A-Z] gt:3276 0" ];
      description = "Extra command line arguments for the exporter.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.prometheus-sunspec-exporter = {
      description = "Prometheus exporter for SunSpec devices";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      environment.PYTHONUNBUFFERED = "1";
      serviceConfig = {
        ExecStart = lib.escapeShellArgs ([
          (lib.getExe cfg.package)
          "start"
          "--port" (toString cfg.port)
          "--sunspec_ip" cfg.sunspecHost
          "--sunspec_port" (toString cfg.sunspecPort)
          "--sunspec_address" (toString cfg.sunspecAddress)
          "--sunspec_model_ids" (lib.concatStringsSep "," cfg.modelIds)
        ] ++ cfg.extraArgs);
        DynamicUser = true;
        Restart = "always";
        RestartSec = 10;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" ];
      };
    };
  };
}

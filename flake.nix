{
  description = "Dyedfox Radio - Desktop internet radio player for KDE Plasma";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "dyedfox-radio";
            version = "unstable-${self.shortRev or "dirty"}";

            src = self;

            format = "other";

            nativeBuildInputs = with pkgs; [
              qt6.wrapQtAppsHook
              wrapGAppsNoGuiHook
              qt6.qttools
              gobject-introspection
            ];

            buildInputs = with pkgs; [
              qt6.qtwayland
              qt6.qtbase
              gst_all_1.gstreamer
              gst_all_1.gst-plugins-base
              gst_all_1.gst-plugins-good
              gst_all_1.gst-plugins-bad
              gst_all_1.gst-libav
            ];

            dependencies = with pkgs.python3Packages; [
              pyqt6
              pyqt6-sip
              requests
              dbus-python
              pygobject3
            ];

            dontWrapQtApps = true;
            dontWrapGApps = true;

            buildPhase = ''
              runHook preBuild
              
              lrelease translations/*.ts
              
              runHook postBuild
            '';

            installPhase = ''
              runHook preInstall

              mkdir -p $out/bin
              mkdir -p $out/lib/dyedfox-radio/translations
              mkdir -p $out/share/applications
              mkdir -p $out/share/icons/hicolor/256x256/apps
              mkdir -p $out/share/icons/hicolor/scalable/apps
              mkdir -p $out/share/licenses/dyedfox-radio

              cp -r api data player tray ui assets main.py $out/lib/dyedfox-radio/
              cp translations/*.qm $out/lib/dyedfox-radio/translations/
              
              cp dyedfox-radio.desktop $out/share/applications/
              cp assets/icons/dyedfox-radio.png $out/share/icons/hicolor/256x256/apps/
              cp assets/icons/dyedfox-radio-tray.svg $out/share/icons/hicolor/scalable/apps/
              cp LICENSE $out/share/licenses/dyedfox-radio/

              echo "#!${pkgs.python3.interpreter}" > $out/bin/dyedfox-radio
              echo "import sys" >> $out/bin/dyedfox-radio
              echo "import runpy" >> $out/bin/dyedfox-radio
              echo "sys.path.insert(0, \"$out/lib/dyedfox-radio\")" >> $out/bin/dyedfox-radio
              echo "runpy.run_path(\"$out/lib/dyedfox-radio/main.py\", run_name=\"__main__\")" >> $out/bin/dyedfox-radio
              chmod +x $out/bin/dyedfox-radio

              runHook postInstall
            '';

            preFixup = ''
              makeWrapperArgs+=("''${qtWrapperArgs[@]}")
              makeWrapperArgs+=("''${gappsWrapperArgs[@]}")
            '';
          };
        }
      );
    };
}

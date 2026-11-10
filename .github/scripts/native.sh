#!/bin/bash
set -euo pipefail

case "$1" in
  import-certificate)
    KEYCHAIN_PATH="$RUNNER_TEMP/build.keychain-db"
    CERT_PATH="$RUNNER_TEMP/certificate.p12"
    printf '%s' "$CERT_BASE64" | base64 --decode -o "$CERT_PATH"
    security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
    security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
    security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
    security import "$CERT_PATH" -P "$CERT_PASSWORD" -A -t cert -f pkcs12 -k "$KEYCHAIN_PATH"
    security list-keychain -d user -s "$KEYCHAIN_PATH" login.keychain
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
    echo "KEYCHAIN_PATH=$KEYCHAIN_PATH" >> "$GITHUB_ENV"
    ;;
  store-credentials)
    xcrun notarytool store-credentials "notarytool-profile" \
      --apple-id "$APPLE_ID" --team-id "$TEAM_ID" --password "$APP_PASSWORD" \
      --keychain "$KEYCHAIN_PATH"
    ;;
  build)
    python3 .github/scripts/native.py clean-macos
    xcodebuild -project "$PRODUCT_NAME.xcodeproj" \
      -target "$PRODUCT_NAME" \
      -configuration Default \
      ARCHS="arm64 x86_64" \
      ONLY_ACTIVE_ARCH=NO \
      MACOSX_DEPLOYMENT_TARGET="$MACOS_DEPLOYMENT_TARGET" \
      build
    ;;
  verify)
    CONTENTS="$BASE_DIR/Plugins/$PRODUCT_NAME.bundle/Contents"
    BIN="$CONTENTS/MacOS/$PRODUCT_NAME"
    test -s "$BIN"
    verify_architectures() {
      local architectures
      architectures=$(lipo -archs "$1")
      case " $architectures " in *" arm64 "*) ;; *) return 1 ;; esac
      case " $architectures " in *" x86_64 "*) ;; *) return 1 ;; esac
    }
    verify_architectures "$BIN"
    python3 .github/scripts/native.py verify-resources
    ;;
  sign)
    BUNDLE_PATH="$BASE_DIR/Plugins/$PRODUCT_NAME.bundle"
    echo "BUNDLE_PATH=$BUNDLE_PATH" >> "$GITHUB_ENV"
    IDENTITY=$(security find-identity -v -p codesigning "$KEYCHAIN_PATH" \
      | grep "Developer ID Application" | head -1 | sed -E 's/.*"(.*)".*/\1/')
    test -n "$IDENTITY"
    codesign --verbose --deep --timestamp --force --options=runtime \
      --sign "$IDENTITY" "$BUNDLE_PATH"
    codesign --verify --deep --strict --verbose=2 "$BUNDLE_PATH"
    ;;
  package)
    ZIP_PATH="${PRODUCT_NAME}-${VERSION}.zip"
    DMG_PATH="${PRODUCT_NAME}-${VERSION}.dmg"
    test ! -e "$ZIP_PATH"
    test ! -e "$DMG_PATH"
    ditto -c -k --keepParent "$BUNDLE_PATH" "$ZIP_PATH"
    hdiutil create -format UDBZ -volname "$PRODUCT_NAME" -srcfolder "$BUNDLE_PATH" "$DMG_PATH"
    for artifact in "$ZIP_PATH" "$DMG_PATH"; do
      response=$(xcrun notarytool submit "$artifact" \
        --keychain-profile "notarytool-profile" --keychain "$KEYCHAIN_PATH" \
        --wait --output-format json)
      printf '%s' "$response" | python3 -c \
        'import json,math,sys
def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate notarization JSON key: " + key)
        result[key] = value
    return result
def invalid_constant(value):
    raise ValueError("Nonfinite notarization JSON value: " + value)
def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite notarization JSON number: " + value)
    return result
result = json.load(sys.stdin, object_pairs_hook=unique_object,
                   parse_constant=invalid_constant, parse_float=finite_float)
sys.exit(0 if isinstance(result,dict) and result.get("status") == "Accepted" else "Notarization was not Accepted: " + repr(result))'
    done
    xcrun stapler staple "$DMG_PATH"
    codesign --verify --deep --strict --verbose=2 "$BUNDLE_PATH"
    python3 .github/scripts/source_version.py --verify-bundle "$BUNDLE_PATH"
    python3 .github/scripts/native.py verify-final
    echo "ZIP_PATH=$ZIP_PATH" >> "$GITHUB_ENV"
    echo "DMG_PATH=$DMG_PATH" >> "$GITHUB_ENV"
    ;;
  *)
    echo "Unknown native phase: $1" >&2
    exit 1
    ;;
esac

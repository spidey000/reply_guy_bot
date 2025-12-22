#!/bin/bash
#
# Docker Build and Push Script with Versioning
# Builds Docker image with --no-cache and pushes to registry
# Maintains version log with changelog
#

set -e

# Configuration
IMAGE_NAME="georgegg0099/reply-guy-bot"
VERSION_FILE="VERSION"
CHANGELOG_FILE="CHANGELOG.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Change to project directory
cd "$PROJECT_DIR"

# Functions
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Get current version or initialize
get_current_version() {
    if [[ -f "$VERSION_FILE" ]]; then
        cat "$VERSION_FILE"
    else
        echo "0.0.0"
    fi
}

# Increment version based on type (major, minor, patch)
increment_version() {
    local version=$1
    local type=$2
    
    IFS='.' read -r major minor patch <<< "$version"
    
    case $type in
        major)
            major=$((major + 1))
            minor=0
            patch=0
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            ;;
        patch|*)
            patch=$((patch + 1))
            ;;
    esac
    
    echo "$major.$minor.$patch"
}

# Log version with changelog entry
log_version() {
    local version=$1
    local changelog=$2
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    echo "==============================================================" >> "$CHANGELOG_FILE"
    echo "Version: $version" >> "$CHANGELOG_FILE"
    echo "Date:    $timestamp" >> "$CHANGELOG_FILE"
    echo "Image:   $IMAGE_NAME:$version" >> "$CHANGELOG_FILE"
    echo "--------------------------------------------------------------" >> "$CHANGELOG_FILE"
    echo "Changes:" >> "$CHANGELOG_FILE"
    echo "$changelog" >> "$CHANGELOG_FILE"
    echo "" >> "$CHANGELOG_FILE"
}

# Show usage
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -t, --type TYPE     Version increment type: major, minor, patch (default: patch)"
    echo "  -m, --message MSG   Changelog message (required)"
    echo "  -v, --version VER   Set specific version (overrides increment)"
    echo "  --skip-push         Build only, don't push to registry"
    echo "  --dry-run           Show what would be done without executing"
    echo "  -h, --help          Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 -t patch -m 'Bug fixes'"
    echo "  $0 -t minor -m 'Added new feature X'"
    echo "  $0 -t major -m 'Breaking changes: API v2'"
    echo "  $0 -v 1.2.3 -m 'Specific version release'"
}

# Parse arguments
VERSION_TYPE="patch"
CHANGELOG_MSG=""
SPECIFIC_VERSION=""
SKIP_PUSH=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -t|--type)
            VERSION_TYPE="$2"
            shift 2
            ;;
        -m|--message)
            CHANGELOG_MSG="$2"
            shift 2
            ;;
        -v|--version)
            SPECIFIC_VERSION="$2"
            shift 2
            ;;
        --skip-push)
            SKIP_PUSH=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate inputs
if [[ -z "$CHANGELOG_MSG" ]]; then
    print_error "Changelog message is required. Use -m or --message"
    usage
    exit 1
fi

if [[ "$VERSION_TYPE" != "major" && "$VERSION_TYPE" != "minor" && "$VERSION_TYPE" != "patch" ]]; then
    print_error "Invalid version type: $VERSION_TYPE. Must be major, minor, or patch"
    exit 1
fi

# Calculate new version
CURRENT_VERSION=$(get_current_version)
if [[ -n "$SPECIFIC_VERSION" ]]; then
    NEW_VERSION="$SPECIFIC_VERSION"
else
    NEW_VERSION=$(increment_version "$CURRENT_VERSION" "$VERSION_TYPE")
fi

# Display build info
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Docker Build & Push"
echo "════════════════════════════════════════════════════════════════"
echo ""
print_info "Image Name:      $IMAGE_NAME"
print_info "Current Version: $CURRENT_VERSION"
print_info "New Version:     $NEW_VERSION"
print_info "Version Type:    $VERSION_TYPE"
print_info "Push to Registry: $([ "$SKIP_PUSH" = true ] && echo 'No' || echo 'Yes')"
echo ""
print_info "Changelog:"
echo "  $CHANGELOG_MSG"
echo ""

if [[ "$DRY_RUN" = true ]]; then
    print_warning "DRY RUN - Commands that would be executed:"
    echo ""
    echo "  sudo docker build -t $IMAGE_NAME:$NEW_VERSION -t $IMAGE_NAME:latest --no-cache ."
    if [[ "$SKIP_PUSH" = false ]]; then
        echo "  sudo docker push $IMAGE_NAME:$NEW_VERSION"
        echo "  sudo docker push $IMAGE_NAME:latest"
    fi
    echo "  echo \"$NEW_VERSION\" > $VERSION_FILE"
    echo "  [Update CHANGELOG.log]"
    echo ""
    exit 0
fi

# Confirm before proceeding
read -p "Proceed with build? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    print_warning "Build cancelled"
    exit 0
fi

echo ""

# Build Docker image
print_info "Building Docker image (no-cache)..."
sudo docker build \
    -t "$IMAGE_NAME:$NEW_VERSION" \
    -t "$IMAGE_NAME:latest" \
    --no-cache \
    .

print_success "Docker image built successfully"

# Push to registry
if [[ "$SKIP_PUSH" = false ]]; then
    print_info "Pushing to registry..."
    sudo docker push "$IMAGE_NAME:$NEW_VERSION"
    sudo docker push "$IMAGE_NAME:latest"
    print_success "Image pushed to registry"
else
    print_warning "Skipping push to registry (--skip-push)"
fi

# Update version file
echo "$NEW_VERSION" > "$VERSION_FILE"
print_success "Version file updated: $NEW_VERSION"

# Log to changelog
log_version "$NEW_VERSION" "$CHANGELOG_MSG"
print_success "Changelog updated"

# Summary
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Build Complete!"
echo "════════════════════════════════════════════════════════════════"
echo ""
print_success "Image: $IMAGE_NAME:$NEW_VERSION"
print_success "Image: $IMAGE_NAME:latest"
echo ""
print_info "To run the new image:"
echo "  sudo docker run -d --env-file .env $IMAGE_NAME:$NEW_VERSION"
echo ""

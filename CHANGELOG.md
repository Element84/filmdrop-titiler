# Changelog

Notable changes to this project are documented here.

## [Unreleased]

### Changed

- Upgraded TiTiler dependencies to 2.4.0.

### Fixed

- Corrected the pytest test discovery path.
- Replaced a deprecated Pydantic validator.

## [0.2.0-alpha.1] - 2026-10-13

Pre-release.

### Added

- Mosaic creation and display endpoints backed by DynamoDB.
- A Docker Compose development environment with LocalStack.

## [0.1.1] - 2026-02-03

### Changed

- Reworked the AWS Lambda build script and deployment package.

## [0.1.0] - 2026-02-02

### Added

- Initial FastAPI-based TiTiler service for COG and STAC imagery.
- AWS Lambda deployment package build.

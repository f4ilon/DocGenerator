param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"

# Create a project and upload a small Python example in the same request.
$project = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/projects" `
    -ContentType "application/json" `
    -Body (@{
        name = "demo-calculator"
        source = @{
            type = "files"
            files = @(
                @{
                    path = "calculator.py"
                    content = @"
class Calculator:
    def add(self, left, right):
        return left + right

    def divide(self, left, right):
        if right == 0:
            raise ValueError("division by zero")
        return left / right
"@
                }
            )
        }
    } | ConvertTo-Json -Depth 8)

$projectId = $project.id
Write-Host "`n1. Created project $($project.name): $projectId"
$project | ConvertTo-Json -Depth 8

# Parse the uploaded source and inspect the extracted project tree.
$parse = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/projects/$projectId/parse"

Write-Host "`n2. Parse result"
$parse | ConvertTo-Json -Depth 8

$tree = Invoke-RestMethod -Method Get -Uri "$BaseUrl/projects/$projectId/tree"
Write-Host "`n3. Parsed project tree"
$tree | ConvertTo-Json -Depth 8

# Generate documentation drafts. Without OPENAI_API_KEY, the API returns
# deterministic placeholder drafts and includes a warning in the response.
$generated = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/projects/$projectId/generate" `
    -ContentType "application/json" `
    -Body (@{ style = "google" } | ConvertTo-Json)

Write-Host "`n4. Generated documentation drafts"
$generated | ConvertTo-Json -Depth 8

$coverage = Invoke-RestMethod -Method Get -Uri "$BaseUrl/projects/$projectId/coverage"
Write-Host "`n5. Documentation coverage"
$coverage | ConvertTo-Json -Depth 8

$search = Invoke-RestMethod `
    -Method Get `
    -Uri "$BaseUrl/search?q=add&project_id=$projectId"

Write-Host "`n6. Search results"
$search | ConvertTo-Json -Depth 8

# Create a Markdown export and show its metadata and history.
# The file itself is deliberately not downloaded by this demo.
$export = Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/projects/$projectId/export" `
    -ContentType "application/json" `
    -Body (@{ format = "markdown" } | ConvertTo-Json)

Write-Host "`n7. Export created (not downloaded)"
$export | ConvertTo-Json -Depth 8

$history = Invoke-RestMethod -Method Get -Uri "$BaseUrl/projects/$projectId/exports"
Write-Host "`n8. Export history"
$history | ConvertTo-Json -Depth 8

Write-Host "`nDemo complete. Download URL (not requested): $BaseUrl$($export.download_url)"

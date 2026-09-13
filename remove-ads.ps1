# remove-ads.ps1
$files = Get-ChildItem -Path "templates" -Filter "*.html"

foreach ($file in $files) {
    $content = Get-Content $file.FullName -Raw
    
    # Remove all ad blocks
    $content = $content -replace '(?s)<!-- Left Sidebar Ad -->.*?<!-- Right Sidebar Ad -->.*?</div>.*?<!-- Hide ads on mobile -->.*?</style>', ''
    
    # Also remove any leftover ad divs
    $content = $content -replace '(?s)<div style="position: fixed; left: 5px;.*?z-index: 100;.*?</div>', ''
    $content = $content -replace '(?s)<div style="position: fixed; right: 5px;.*?z-index: 100;.*?</div>', ''
    $content = $content -replace '(?s)<style>.*?@media.*?z-index: 100.*?</style>', ''
    
    Set-Content -Path $file.FullName -Value $content
    Write-Host "✅ Cleaned: $($file.Name)"
}

Write-Host "🎉 All ads removed!"
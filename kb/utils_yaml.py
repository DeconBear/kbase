import yaml
import re
from pathlib import Path

def parse_frontmatter(file_path: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file.
    
    Returns:
        tuple: (metadata_dict, markdown_content)
    """
    if not file_path.exists():
        return {}, ""
        
    content = file_path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)", content, re.DOTALL)
    
    if match:
        yaml_text = match.group(1)
        markdown_text = match.group(2)
        try:
            metadata = yaml.safe_load(yaml_text) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            return metadata, markdown_text
        except yaml.YAMLError:
            return {}, content
    else:
        return {}, content

def write_frontmatter(file_path: Path, metadata: dict, markdown_content: str):
    """Write YAML frontmatter and markdown content to a file."""
    # Ensure parent dir exists
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    yaml_text = yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False)
    # Ensure yaml_text doesn't have trailing newlines before the end delimiter
    yaml_text = yaml_text.strip()
    
    new_content = f"---\n{yaml_text}\n---\n{markdown_content}"
    # Ensure it ends with newline
    if not new_content.endswith("\n"):
        new_content += "\n"
        
    file_path.write_text(new_content, encoding="utf-8")

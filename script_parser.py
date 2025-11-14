import os
import re


class ScriptParser:
    def __init__(self, script_path):
        self.script_path = script_path
        self.plugins = []
        self.addons = []
        self.exec_binds = []
        self.exec_aliases = []
        self.exec_other = []
        self.wait_time = 8
        self.config_commands = []
        
    def parse(self):
        if not os.path.exists(self.script_path):
            return False
            
        with open(self.script_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        after_wait = False
        
        for line in lines:
            stripped = line.strip()
            
            # Skip empty lines and pure comments (no command)
            if not stripped or (stripped.startswith('#') and not stripped.startswith('#/')):
                continue
            
            # Check if it's a commented command
            is_commented = stripped.startswith('#/')
            if is_commented:
                # Remove the leading #
                stripped = stripped[1:].strip()
            
            enabled = not is_commented
            
            # Parse /load commands (plugins)
            if stripped.startswith('/load '):
                plugin_name = stripped[6:].strip()
                self.plugins.append({'name': plugin_name, 'enabled': enabled, 'original': line.rstrip()})
            
            # Parse /addon load commands (addons)
            elif stripped.startswith('/addon load '):
                addon_parts = stripped[12:].strip().split()
                addon_name = addon_parts[0]
                addon_args = ' '.join(addon_parts[1:]) if len(addon_parts) > 1 else ''
                self.addons.append({'name': addon_name, 'args': addon_args, 'enabled': enabled, 'original': line.rstrip()})
            
            # Parse /exec commands
            elif stripped.startswith('/exec '):
                exec_path = stripped[6:].strip()
                
                # Categorize exec by path
                if 'bind' in exec_path.lower():
                    self.exec_binds.append({'path': exec_path, 'enabled': enabled, 'original': line.rstrip(), 'type': 'exec'})
                elif 'alias' in exec_path.lower():
                    self.exec_aliases.append({'path': exec_path, 'enabled': enabled, 'original': line.rstrip(), 'type': 'exec'})
                else:
                    self.exec_other.append({'path': exec_path, 'enabled': enabled, 'original': line.rstrip(), 'type': 'exec'})
            
            # Parse /bind commands
            elif stripped.startswith('/bind '):
                bind_command = stripped[6:].strip()
                self.exec_binds.append({'path': bind_command, 'enabled': enabled, 'original': line.rstrip(), 'type': 'bind'})
            
            # Parse /alias commands
            elif stripped.startswith('/alias '):
                alias_command = stripped[7:].strip()
                self.exec_aliases.append({'path': alias_command, 'enabled': enabled, 'original': line.rstrip(), 'type': 'alias'})
            
            # Parse /wait command
            elif stripped.startswith('/wait '):
                wait_value = stripped[6:].strip()
                try:
                    self.wait_time = int(wait_value)
                    after_wait = True
                except ValueError:
                    pass
            
            # Everything after /wait is config
            elif after_wait and stripped.startswith('/'):
                self.config_commands.append({'command': stripped, 'enabled': enabled, 'original': line.rstrip()})
        
        return True
    
    def save(self):
        lines = []
        
        # File header
        lines.extend([
            '##########################################################################',
            '#',
            '# Ashita v4 Script',
            '#',
            '# This script is executed at the start of the game to allow for the user',
            '# to configure their game instance automatically. Use this script to load',
            '# plugins, addons or adjust different settings as you see fit.',
            '#',
            '# File Syntax:',
            '#',
            '#  - Comments start with \'#\'.',
            '#  - Commands start with \'/\'.',
            '#',
            '##########################################################################',
            ''
        ])
        
        # Plugins section
        if self.plugins:
            lines.extend([
                '##########################################################################',
                '#',
                '# Load Plugins',
                '#',
                '##########################################################################',
                ''
            ])
            for plugin in self.plugins:
                prefix = '' if plugin['enabled'] else '#'
                lines.append(f"{prefix}/load {plugin['name']}")
            lines.append('')
        
        # Addons section
        if self.addons:
            lines.extend([
                '##########################################################################',
                '#',
                '# Load Addons',
                '#',
                '##########################################################################',
                ''
            ])
            for addon in self.addons:
                prefix = '' if addon['enabled'] else '#'
                args = f" {addon['args']}" if addon['args'] else ''
                lines.append(f"{prefix}/addon load {addon['name']}{args}")
            lines.append('')
        
        # Keybinds/Alias section
        has_exec = self.exec_binds or self.exec_aliases or self.exec_other
        if has_exec:
            lines.extend([
                '##########################################################################',
                '#',
                '# Set Keybinds and Alias',
                '#',
                '##########################################################################',
                ''
            ])
            
            for exec_item in self.exec_binds + self.exec_aliases + self.exec_other:
                prefix = '' if exec_item['enabled'] else '#'
                item_type = exec_item.get('type', 'exec')
                
                if item_type == 'exec':
                    lines.append(f"{prefix}/exec {exec_item['path']}")
                elif item_type == 'bind':
                    lines.append(f"{prefix}/bind {exec_item['path']}")
                elif item_type == 'alias':
                    lines.append(f"{prefix}/alias {exec_item['path']}")
            lines.append('')
        
        # Wait command and config section
        lines.extend([
            '##########################################################################',
            '#',
            '# Plugin and Addon Configurations',
            '#',
            '# Use this section to configure loaded plugins, addons and Ashita.',
            '#',
            '# Important: The wait here is required! If you remove it, addons will not',
            '# see any commands inside of this file!',
            '#',
            '##########################################################################',
            f'/wait {self.wait_time}',
            '##########################################################################',
            ''
        ])
        
        # Config commands
        for cmd in self.config_commands:
            prefix = '' if cmd['enabled'] else '#'
            lines.append(f"{prefix}{cmd['command']}")
        
        # Write to file
        with open(self.script_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        return True
    
    def get_all_scripts(scripts_dir):
        if not os.path.exists(scripts_dir):
            return []
        
        scripts = []
        for file in os.listdir(scripts_dir):
            if file.endswith('.txt'):
                scripts.append(file)
        
        return sorted(scripts)

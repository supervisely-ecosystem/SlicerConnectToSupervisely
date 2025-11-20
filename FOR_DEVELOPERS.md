## Build Configuration - Github Workflows

### Custom Slicer VERSION and REVISION

When creating a release, you can specify custom Slicer VERSION and REVISION in the release description. This is useful when you need to build installation files for a specific Slicer version.

To use this feature, add the following lines to your release description:

```
VERSION: 5.6.2
REVISION: 32438
```

If VERSION and REVISION are not specified in the release description, they will be automatically fetched from the latest stable Slicer release at https://download.slicer.org.

## Development Setup

### Setting Up Local Development Module

To develop and test the extension locally without installing it as a packaged extension:

1. Open **3D Slicer**
2. Go to **Edit** → **Application Settings**
3. Navigate to **Modules** section
4. In **Additional module paths**, click the **Add** button (➕)
5. Browse and select the `ConnectToSupervisely` directory from your local repository
6. Click **OK** and restart 3D Slicer
7. After restart, your modules should appear in the modules list

This allows you to edit the Python code and see changes immediately after reloading the module (Ctrl+R in the module or restart Slicer).

## Debugging

### Debugging with VS Code

You can debug 3D Slicer extensions using VS Code with the SlicerDebuggingTools extension. Follow the setup instructions here: https://github.com/SlicerRt/SlicerDebuggingTools

### Key Steps:

1. Set up local development module (see above)
2. Install SlicerDebuggingTools extension in 3D Slicer
3. Configure VS Code launch settings to attach to Slicer's Python process
4. Set breakpoints in your Python code
5. Start debugging session from VS Code and run your module in Slicer

### Useful Resources

- [3D Slicer Release Details](https://github.com/Slicer/Slicer/wiki/Release-Details) - Information about different Slicer versions and revisions
- [SlicerDebuggingTools](https://github.com/SlicerRt/SlicerDebuggingTools) - VS Code debugging integration for 3D Slicer extensions

import copy
import logging
import os
from functools import partial
from json import load
from pathlib import Path
from typing import Literal

import qt
import slicer
from slicer.ScriptedLoadableModule import ScriptedLoadableModuleLogic

try:
    from supervisely import (
        Api,
        KeyIdMap,
        Mask3D,
        ProjectMeta,
        TagApplicableTo,
        TagValueType,
        VolumeAnnotation,
        VolumeObject,
        VolumeProject,
        VolumeTag,
    )
    from supervisely.api.labeling_job_api import LabelingJobInfo
    from supervisely.io.fs import get_file_name_with_ext, list_files, mkdir, remove_dir

    import dotenv  # isort:skip
except ModuleNotFoundError:
    pass


from moduleLib import (
    RESTORE_LIB_FILE,
    InputDialog,
    SuperviselyDialog,
    clear,
    log_method_call,
    log_method_call_args,
    segmentClass,
    volumeClass,
)

# --------------------------------------- Global Variables --------------------------------------- #

ENV_FILE_PATH = os.path.join(Path.home(), "supervisely_slicer.env")
DEFAULT_WORK_DIR = os.path.join(Path.home(), "supervisely_slicer_data")


class BaseLogic(ScriptedLoadableModuleLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, ui) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""

        ScriptedLoadableModuleLogic.__init__(self)
        self.api = None
        self.teamList = None
        self.volume = None
        self.jobList = None
        self.activeTeam = None
        self.activeJob: LabelingJobInfo = None
        self.savePath = None
        self.ann = None
        self.tagMetas = None
        self.keyIdMap = None
        self.ui = ui
        self.skipSegmentStatusCheck = self.ui.skipSegmentStatusCheck.isChecked()

        envWorkDir = dotenv.get_key(ENV_FILE_PATH, "WORKING_DIR")
        if envWorkDir and os.path.exists(envWorkDir):
            self.workingDir = dotenv.get_key(ENV_FILE_PATH, "WORKING_DIR")
        elif envWorkDir and not os.path.exists(envWorkDir):
            mkdir(envWorkDir)
            self.workingDir = envWorkDir
        else:
            self.workingDir = DEFAULT_WORK_DIR
            dotenv.set_key(ENV_FILE_PATH, "WORKING_DIR", self.workingDir)
        self.ui.workingDirButton.text = str(self.workingDir)
        self.ui.workingDirButton.directory = self.workingDir

    @log_method_call
    def configureUI(self) -> None:

        self.ui.addedTagsLayout.setAlignment(qt.Qt.AlignTop)
        self.ui.availableTagsLayout.setAlignment(qt.Qt.AlignTop)
        is_loaded = dotenv.load_dotenv(ENV_FILE_PATH, override=True)
        is_keep_logged = os.getenv("KEEP_LOGGED")
        if is_loaded and is_keep_logged == "True":
            self.api = Api.from_env()
            if self.api:
                self.userName = os.getenv("LOGIN_NAME")
                if self.userName is None:
                    self._getUserName()
                    dotenv.set_key(ENV_FILE_PATH, "LOGIN_NAME", self.userName)
                self.ui.loginName.text = f"You are logged in Supervisely as {self.userName}"
                self.ui.serverAddress.hide()
                self.ui.login.hide()
                self.ui.password.hide()
                self.ui.serverAddressLabel.hide()
                self.ui.loginLabel.hide()
                self.ui.passwordLabel.hide()
                self.ui.emptySpaceRememberLogin.hide()
                self.ui.rememberLogin.hide()
                self.ui.connectButton.text = "Disconnect"
                self._activateTeamSelection()
        else:
            self.ui.loginName.hide()
        self.ui.refreshJobsButton.setEnabled(False)
        self.ui.startJobButton.setEnabled(False)
        self.ui.syncCurrentJobButton.setEnabled(False)
        self.ui.workingDirButton.setFixedHeight(26)
        if os.path.exists(RESTORE_LIB_FILE):
            self.ui.restoreLabel.show()
            self.ui.restoreLibrariesButton.show()
            self.ui.horizontalLine.show()

    @log_method_call
    def logIn(self) -> None:

        if self.ui.connectButton.text == "Disconnect":
            self.ui.connectButton.text = "Connect"
            self.ui.serverAddress.enabled = True
            self.ui.login.enabled = True
            self.ui.password.enabled = True
            self.ui.serverAddress.text = ""
            self.ui.login.text = ""
            self.ui.password.text = ""
            self.ui.loginName.hide()
            self.ui.rememberLogin.setChecked(False)
            self.ui.serverAddress.show()
            self.ui.login.show()
            self.ui.password.show()
            self.ui.serverAddressLabel.show()
            self.ui.loginLabel.show()
            self.ui.passwordLabel.show()
            self.ui.emptySpaceRememberLogin.show()
            self.ui.rememberLogin.show()
            self.ui.rememberLogin.enabled = True
            dotenv.set_key(ENV_FILE_PATH, "KEEP_LOGGED", "False")
            dotenv.unset_key(ENV_FILE_PATH, "LOGIN_NAME")
            self.api = None
            self._deactivateTeamSelection()
            clear(self)
            return

        activeModule = dotenv.get_key(ENV_FILE_PATH, "ACTIVE_SLY_MODULE")

        self.api = Api.from_credentials(
            server_address=self.ui.serverAddress.text,
            login=self.ui.login.text,
            password=self.ui.password.text,
            override=self.ui.rememberLogin.isChecked(),
            env_file=ENV_FILE_PATH,
        )

        if activeModule:
            dotenv.set_key(ENV_FILE_PATH, "ACTIVE_SLY_MODULE", f"{activeModule}")

        self.changeWorkingDir()
        if self.ui.rememberLogin.isChecked():
            self._getUserName()
            dotenv.set_key(ENV_FILE_PATH, "KEEP_LOGGED", "True")
            dotenv.set_key(ENV_FILE_PATH, "LOGIN_NAME", self.userName)
            self.ui.serverAddress.hide()
            self.ui.login.hide()
            self.ui.password.hide()
            self.ui.serverAddressLabel.hide()
            self.ui.loginLabel.hide()
            self.ui.passwordLabel.hide()
            self.ui.emptySpaceRememberLogin.hide()
            self.ui.rememberLogin.hide()
            self.ui.loginName.text = f"You are logged in Supervisely as {self.userName}"
            self.ui.loginName.show()

        if self.api:
            self.ui.serverAddress.enabled = False
            self.ui.login.enabled = False
            self.ui.password.enabled = False
            self.ui.rememberLogin.enabled = False

            self.ui.connectButton.text = "Disconnect"
            self._activateTeamSelection()

    @log_method_call
    def changeLabelingButtonState(self) -> None:
        if (
            self.ui.jobSelector.currentText == "No jobs available"
            or self.ui.jobSelector.currentText == "Select..."
        ):
            self.ui.startJobButton.setEnabled(False)
        else:
            self.ui.startJobButton.setEnabled(True)

    @log_method_call
    def setActiveJob(self) -> None:
        self.activeJob = self._getItemFromSelector(self.jobList, self.ui.jobSelector.currentText)
        self.ui.activeJob.setChecked(False)
        self.ui.activeJob.setEnabled(False)

    @log_method_call
    def fulfillInfo(self) -> None:
        self.ui.descriptionLabel.clear()
        self.ui.readmeLabel.clear()
        if self.activeJob.description:
            self.ui.descriptionLabel.setText(self.activeJob.description)
        else:
            self.ui.descriptionLabel.setText("No description")
        if self.activeJob.readme:
            self.ui.readmeLabel.setText(self.activeJob.readme)
        else:
            self.ui.readmeLabel.setText("No readme")

    @log_method_call
    def downloadData(self) -> None:

        self._refreshJobInfo()

        self.savePath = os.path.join(self.workingDir, f"{self.activeJob.project_id}")

        if os.path.exists(self.savePath):
            if SuperviselyDialog(
                """
You already have locally existing files for this project.
For correct operation and synchronization you need to download the data from the server again.
The local data will be overwritten by the downloaded data.

Do you want to continue?""",
                type="confirm",
            ):
                remove_dir(self.savePath)
            else:
                return False

        self.ui.progressBar.setMaximum(len(self.activeJob.entities))
        self.ui.downloadingText.show()
        self.ui.progressBar.show()

        self._dowloadProject()

        self.ui.downloadingText.hide()
        self.ui.progressBar.hide()

        volumes = list_files(f"{self.savePath}/{self.activeJob.dataset_name}/volume")
        volumes = [get_file_name_with_ext(volume) for volume in volumes]
        self.ui.volumeSelector.blockSignals(True)
        self.ui.volumeSelector.clear()
        self.ui.volumeSelector.addItem("Select...")
        self.ui.volumeSelector.currentText = "Select..."
        self.ui.volumeSelector.addItems(volumes)
        self._setVolumeIcon()
        self._setProgressInfo()
        self.ui.volumeSelector.blockSignals(False)
        self.ui.volumeSelector.enabled = True
        self.ui.startJobButton.setEnabled(False)
        self.ui.syncCurrentJobButton.setEnabled(True)
        self.ui.activeJob.setEnabled(True)
        self.ui.activeJob.setChecked(True)
        return True

    @log_method_call
    def loadVolumes(self) -> None:
        clear(self, local_data=False)

        maskDir = f"{self.savePath}/{self.activeJob.dataset_name}/mask/{self.ui.volumeSelector.currentText}"

        self.volume = volumeClass(
            maskDir=maskDir,
            availableTagsLayout=self.ui.availableTagsLayout,
            addedTagsLayout=self.ui.addedTagsLayout,
        )
        for entity in self.activeJob.entities:
            if entity.get("name") == self.ui.volumeSelector.currentText:
                self.volume.id = entity.get("id")
                break
        self.volume.name = self.ui.volumeSelector.currentText
        self.volume.node = slicer.util.loadVolume(
            f"{self.savePath}/{self.activeJob.dataset_name}/volume/{self.volume.name}",
            {"show": True},
        )
        self.volume.node.SetName(self.volume.name)

        self._createAnnObject()
        self.createTagButtons()
        self.populateVolumeWithTags()
        self.populateAddedTagsUI()

        self.volume.createSegmentationsOnLoad(logic=self)

        for fig in self.ann.spatial_figures:
            maskKey = fig.key().hex
            maskClassName = fig.parent_object.obj_class.name
            maskObjectId = self.keyIdMap.get_object_id(fig.parent_object.key())
            if maskClassName not in self.activeJob.classes_to_label:
                continue
            maskClassColor = fig.parent_object.obj_class.color
            maskClassColor = (
                maskClassColor[0] / 255,
                maskClassColor[1] / 255,
                maskClassColor[2] / 255,
            )
            segmentation_names = self.volume.getSegmentationNames()

            if maskClassName in segmentation_names:
                segmentation = self.volume.getSegmentationByName(maskClassName)
                segment = segmentClass(
                    f"{maskDir}/{maskKey}.nrrd",
                    maskClassName,
                    maskClassColor,
                    maskKey,
                    segmentationNode=segmentation.segmentationNode,
                    objectId=maskObjectId,
                )
                segmentation.appendSegment(segment)
            else:
                self.volume.createSegmentation(
                    maskDir,
                    maskKey,
                    maskClassName,
                    maskClassColor,
                    maskObjectId,
                )
        self._activateJobButtons()

    @log_method_call
    def loadAnnotations(self) -> None:
        """Unised method."""
        self.removeAnnotaionsFromScene()
        self.volume.addToScene()

    @log_method_call
    def removeVolumeFromScene(self) -> None:
        """Deprecated method. Use slicer.mrmlScene.Clear() instead."""
        volumeNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLScalarVolumeNode")
        self._removeNodesFromScene(volumeNodes)

    @log_method_call
    def removeAnnotaionsFromScene(self) -> None:
        """Deprecated method. Use slicer.mrmlScene.Clear() instead."""
        labelMapVolumeNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLLabelMapVolumeNode")
        segmentationNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLSegmentationNode")
        displayNodes = slicer.mrmlScene.GetNodesByClass("vtkMRMLSegmentationDisplayNode")
        self._removeNodesFromScene(displayNodes)
        self._removeNodesFromScene(labelMapVolumeNodes)
        self._removeNodesFromScene(segmentationNodes)

    @log_method_call
    def saveAnnotations(self) -> None:
        if self.volume:
            logic = slicer.modules.segmentations.logic()
            for segmentation in self.volume.segmentations:
                segmentation.populateSegments()
                segmentation.markSegmentsForDeletion()
                for segment in segmentation.segments:
                    if not segment.delete:
                        if self.skipSegmentStatusCheck:
                            segment.save(segmentation.maskDir)
                        else:
                            status = logic.GetSegmentStatus(segment.segment)
                            if status == slicer.vtkSlicerSegmentationsModuleLogic.Completed:
                                segment.save(segmentation.maskDir)
                            elif status == slicer.vtkSlicerSegmentationsModuleLogic.InProgress:
                                segment.askForSave(segmentation.maskDir)

    @log_method_call_args
    def changeVolumeStatus(self, status) -> None:
        for entity in self.activeJob.entities:
            if entity["name"] == self.volume.name:
                volumeId = entity["id"]
        self.api.labeling_job.set_entity_review_status(self.activeJob.id, volumeId, status)

    @log_method_call_args
    def changeJobStatus(
        self, status: Literal["pending", "in_progress", "on_review", "completed", "stopped"]
    ) -> None:
        """Change job status to 'status'

        Args:
            status (str): New status. Can be "pending", "in_progress", "on_review", "completed", "stopped"
        """
        currentStatus = self.api.labeling_job.get_status(self.activeJob.id).value
        setStatus = self.api.labeling_job.set_status
        if currentStatus == "pending" and status in ["in_progress", "stopped"]:
            setStatus(self.activeJob.id, status)
        elif currentStatus == "in_progress" and status in ["on_review", "stopped"]:
            setStatus(self.activeJob.id, status)
        elif currentStatus == "on_review" and status in ["completed", "stopped"]:
            setStatus(self.activeJob.id, status)

    @log_method_call
    def uploadAnnObjectChangesToServer(self) -> None:
        from uuid import UUID

        from numpy import bool_, zeros

        dialog = SuperviselyDialog("\nSaving changes to the server...\n", type="comment")

        for segmentation in self.volume.segmentations:
            for segment in segmentation.segments:
                if not segment.delete:
                    if segment.maskKey:
                        # Create 3D Mmask figure and upload to project on server
                        mask_uuid = UUID(segment.maskKey)
                        _, mask_bytes = Mask3D._bytes_from_nrrd(segment.path)
                        self.api.volume.figure.upload_sf_geometries(
                            [mask_uuid], {mask_uuid: mask_bytes}, self.keyIdMap
                        )
                    else:
                        try:
                            # Create new figure from file that doesn't exist on server
                            new_figure = Mask3D.create_from_file(segment.path)
                        except ValueError as e:
                            if "Instead got [0]" in e.args[0]:
                                e.args = (f"Segment {segment.name} is empty",)
                            raise e
                        figure_class = self.projectMeta.obj_classes.get(segmentation.name)
                        # Create new object of corresponding class
                        new_object = VolumeObject(figure_class, mask_3d=new_figure)
                        volumeInfo = self.api.volume.get_info_by_name(
                            parent_id=self.activeJob.dataset_id, name=self.volume.name
                        )
                        # Create new annotation object that contains only new object
                        new_ann = VolumeAnnotation(
                            volumeInfo.meta,
                            objects=[new_object],
                            spatial_figures=[new_object.figure],
                        )
                        # Create copy of keyIdMap object to compare it with new one after upload
                        oldKeyIdMapDict = copy.deepcopy(self.keyIdMap).to_dict()
                        # Upload new annotation object to server
                        self.api.volume.annotation.append(volumeInfo.id, new_ann, self.keyIdMap)

                        # TODO Replace with the less expensive method
                        # obj_id = VolumeObjectApi.append_bulk(volumeInfo.id, [new_object], self.keyIdMap)
                        # VolumeFigureApi._append_bulk_mask3d(
                        #     volumeInfo.id, [new_object.figure], self.keyIdMap
                        # )

                        # keyIdMap object updated during upload, so we can compare it with old one
                        newKeyIdMapDict = self.keyIdMap.to_dict()
                        # Get maskKey and objectId of new segment
                        segment.maskKey = list(
                            set(newKeyIdMapDict["figures"]) - set(oldKeyIdMapDict["figures"])
                        ).pop()
                        objectKey = list(
                            set(newKeyIdMapDict["objects"]) - set(oldKeyIdMapDict["objects"])
                        ).pop()
                        segment.objectId = self.keyIdMap.get_object_id(UUID(objectKey))
                        # Update segment path in segment object
                        newPath = os.path.dirname(segment.path) + f"/{segment.maskKey}.nrrd"
                        os.rename(segment.path, newPath)
                        segment.path = newPath
                        # Replace geometry data to conform to the Mask3D format in json file
                        new_object.figure.geometry.data = zeros((3, 3, 3), bool_)
                        # Update annotation and keyIdMap files
                        self.ann = self.ann.add_objects([new_object])
                        self.ann.dump_json(
                            f"{self.savePath}/{self.activeJob.dataset_name}/ann/{self.volume.name}.json",
                            key_id_map=self.keyIdMap,
                        )
                        self.keyIdMap.dump_json(f"{self.savePath}/key_id_map.json")

        # ---------------------------------- Remove Objects From Server ---------------------------------- #

        # Collect objects to remove
        objects_to_remove = []
        object_keys_to_remove = []
        figures_to_remove = []
        figure_keys_to_remove = []

        for segmentation in self.volume.segmentations:
            for segment in segmentation.segments:
                if segment.delete:
                    objects_to_remove.append(segment.objectId)
                    object_keys_to_remove.append(self.keyIdMap.get_object_key(segment.objectId))
                    figures_to_remove.append((segmentation, segment))
                    figure_keys_to_remove.append(UUID(segment.maskKey))
        if objects_to_remove:
            # Remove objects from server
            self.api.volume.object.remove_batch(objects_to_remove)

            # Remove objects from annotation object
            self.ann = self.ann.remove_objects(object_keys_to_remove)

            # Remove objects and its figures from keyIdMap object
            for object_id in objects_to_remove:
                self.keyIdMap.remove_object(id=object_id)

            # Remove figures from keyIdMap object
            for key in figure_keys_to_remove:
                self.keyIdMap.remove_figure(key=key)

            # Update annotation and keyIdMap files
            self.ann.dump_json(
                f"{self.savePath}/{self.activeJob.dataset_name}/ann/{self.volume.name}.json",
                key_id_map=self.keyIdMap,
            )
            self.keyIdMap.dump_json(f"{self.savePath}/key_id_map.json")

            # Remove segments in Slicer
            for segmentation, segment in figures_to_remove:
                segmentation.removeSegmentBySegment(segment)

        dialog.close()

    # -------------------------------------- Tag Methods Section ------------------------------------- #

    @staticmethod
    def onTagButtonAdd(button, logic, checked, value=None):
        """
        Run processing when user clicks "Add" button.
        Value used only for population of already added tags.
        """
        newButton = qt.QPushButton()
        newText = button.text
        if value is None:
            if ": STR" in newText:
                dialog = InputDialog(icon=button.icon, title=newText, label="Enter text")
                if not dialog.execute_and_assign_tag(newText, ": STR    ➡️", newButton, logic):
                    return
            elif ": NUM" in newText:
                dialog = InputDialog(
                    validate="number", icon=button.icon, title=newText, label="Enter number"
                )
                if not dialog.execute_and_assign_tag(newText, ": NUM    ➡️", newButton, logic):
                    return
            elif ": ONEOF" in newText:
                tagName = newText.split(": ONEOF")[0]
                for tag in logic.tagMetas:
                    if tag.name == tagName:
                        tagOptions = tag.possible_values
                        continue
                dialog = InputDialog(
                    options=tagOptions,
                    icon=button.icon,
                    title=newText,
                    label="Choose one of the options",
                )
                if not dialog.execute_and_assign_tag(newText, ": ONEOF    ➡️", newButton, logic):
                    return
            else:
                newButton.setText(newText.replace("    ➡️", "    🗑️"))
                tag = {"name": newText.replace("    ➡️", ""), "value": None}
                if not logic.volume.hasTag(tag["name"]):
                    logic.volume.assignTag(tag)
                else:
                    InputDialog.show_notification_none(
                        button.icon, f"Tag [{tag['name']}] already exists.", 2000
                    )
                    return
        else:
            for clearItem in ["    ➡️", "    🗑️", ": ONEOF", ": NUM", ": STR"]:
                newText = newText.replace(clearItem, "")
            if value == "None":
                value = None
                newButton.setText(f"{newText}    🗑️")
            else:
                newButton.setText(f"{newText}: {value}    🗑️")
            tag = {"name": newText, "value": value}
            if not logic.volume.hasTag(tag["name"]):
                logic.volume.assignTag(tag)
        newButton.setIcon(button.icon)
        newButton.setFixedHeight(button.height)
        newButton.setStyleSheet("text-align: left;")
        logic.ui.addedTagsLayout.addWidget(newButton)
        logic.ui.noneTags_right.hide()
        newButton.clicked.connect(partial(logic.onTagButtonRemove, newButton, logic))

    @staticmethod
    def onTagButtonRemove(button, logic, checked):
        logic.ui.addedTagsLayout.removeWidget(button)
        if ": " in button.text:
            name, value = button.text.split(": ")
            value = value.replace("    🗑️", "")
        else:
            name = button.text
            value = None
            name = name.replace("    🗑️", "")
        for tagButton in logic.volume.tagButtons:
            if name in tagButton.text and ": NUM" in tagButton.text:
                value = int(value)
                continue
        logic.volume.removeTag(name, value)
        button.deleteLater()

    @log_method_call
    def createTagButtons(self):

        self.tagMetas = self.projectMeta.tag_metas.items()
        self._createdButtons = 0
        for tagMeta in self.tagMetas:
            if tagMeta.name in self.activeJob.tags_to_label and tagMeta.applicable_to in (
                TagApplicableTo.IMAGES_ONLY,
                TagApplicableTo.ALL,
            ):
                if tagMeta.value_type == TagValueType.NONE:
                    button = qt.QPushButton(tagMeta.name + "    ➡️")
                elif tagMeta.value_type == TagValueType.ANY_STRING:
                    button = qt.QPushButton(tagMeta.name + ": STR" + "    ➡️")
                elif tagMeta.value_type == TagValueType.ANY_NUMBER:
                    button = qt.QPushButton(tagMeta.name + ": NUM" + "    ➡️")
                elif tagMeta.value_type == TagValueType.ONEOF_STRING:
                    button = qt.QPushButton(tagMeta.name + ": ONEOF" + "    ➡️")
                button.setStyleSheet("text-align: left;")
                height = button.sizeHint.height()
                self.ui.availableTagsLayout.addWidget(button)
                button.clicked.connect(partial(self.onTagButtonAdd, button, self))
                button.setIcon(self._createColorIcon(tagMeta.color))
                button.setFixedHeight(height)
                self.volume.tagButtons.append(button)
                self._createdButtons += 1
        if self._createdButtons > 0:
            self.ui.noneTags_left.hide()

    @log_method_call
    def populateVolumeWithTags(self) -> None:
        for tagInfo in self.ann.tags:
            tag = {"name": tagInfo.name, "value": tagInfo.value}
            self.volume.tags.append(tag)

    @log_method_call
    def populateAddedTagsUI(self) -> None:
        for tag in self.volume.tags:
            for button in self.volume.tagButtons:
                if tag["name"] == button.text.replace("    ➡️", "").split(": ")[0]:
                    if tag["value"]:
                        self.onTagButtonAdd(button, self, False, tag["value"])
                    else:
                        self.onTagButtonAdd(button, self, False, "None")
                    continue

    @log_method_call
    def uploadTagsChangesToServer(self) -> None:
        if self.volume.tagsChanged:

            # Get all tags from volume and annotation object
            newTags = []
            forDeleteTags = []
            volumeTagsSet = set((tag["name"], tag["value"]) for tag in self.volume.tags)
            annTagsSet = set((tag.meta.name, tag.value) for tag in self.ann.tags)
            # Get tags that should be added and removed
            tagsToAdd = volumeTagsSet - annTagsSet
            tagsToRemove = annTagsSet - volumeTagsSet
            # Convert sets to lists of dictionaries
            tagsToAdd = [{"name": name, "value": value} for name, value in tagsToAdd]
            tagsToRemove = [{"name": name, "value": value} for name, value in tagsToRemove]

            for tag in tagsToRemove:
                # Get tag key for current tag (this tag also exists on server)
                tagKey = [
                    annTag.key()
                    for annTag in self.ann.tags
                    if annTag.name == tag["name"] and annTag.value == tag["value"]
                ][0]
                tagId = self.keyIdMap.get_tag_id(tagKey)
                self.api.volume.tag.remove_from_volume(tagId)
                self.keyIdMap.remove_tag(tagKey)
                # Add tag key to list of tags that should be removed from annotation object
                forDeleteTags.append(tagKey)

            for tag in tagsToAdd:
                # Get tagMeta object for current tag
                tagMeta = self.projectMeta.tag_metas.get(tag["name"])
                # Upload new tag to server and get its id
                value = tag["value"]
                newTagId = self.api.volume.tag.append_to_volume(
                    volume_id=self.volume.id,
                    tag_id=tagMeta.sly_id,
                    value=value,
                    tag_meta=tagMeta,
                )
                # Create new tag object that will be added to annotation object to update it to state as on server
                # without downloading and replace full annotation object
                newTag = VolumeTag(tagMeta, value=value, sly_id=newTagId)
                self.keyIdMap.add_tag(newTag.key(), newTag.sly_id)
                # Add new tag object to list of tags that should be added to annotation object
                newTags.append(newTag)

            # Update annotation object with changes
            self.ann = self.ann.remove_tags(forDeleteTags)
            self.ann = self.ann.add_tags(newTags)

            # Update annotation and keyIdMap files
            self.ann.dump_json(
                self.savePath + f"/{self.activeJob.dataset_name}/ann/{self.volume.name}.json"
            )
            self.keyIdMap.dump_json(self.savePath + "/key_id_map.json")
            # Reset tagsChanged flag
            self.volume.tagsChanged = False
        else:
            logging.info("Tags are not changed, nothing to upload")
            return

    # ----------------------------------------- Other Methods ---------------------------------------- #

    @log_method_call
    def removeLocalData(self) -> None:

        if self.savePath and os.path.exists(self.savePath):
            remove_dir(self.savePath)
            self.savePath = None

    @log_method_call
    def changeWorkingDir(self) -> None:
        self.workingDir = self.ui.workingDirButton.directory
        self.ui.workingDirButton.text = self.ui.workingDirButton.directory
        dotenv.set_key(ENV_FILE_PATH, "WORKING_DIR", self.workingDir)

    # ------------------------------------------ UI Methods ------------------------------------------ #

    @log_method_call
    def setVolumeStatusUI(self) -> None:
        self._refreshJobInfo()
        self._setVolumeIcon()
        self._setProgressInfo()

    @log_method_call
    def _activateTeamSelection(self) -> None:
        self.ui.downloadingText.hide()
        self.ui.progressBar.hide()
        self.ui.teamJobs.setChecked(True)
        self.ui.teamJobs.setEnabled(True)
        self.ui.teamSelector.blockSignals(True)
        self.ui.jobSelector.blockSignals(True)
        if "Select..." not in [
            self.ui.teamSelector.itemText(i) for i in range(self.ui.teamSelector.count)
        ]:
            self.ui.teamSelector.addItem("Select...")

        if "Select..." not in [
            self.ui.jobSelector.itemText(i) for i in range(self.ui.jobSelector.count)
        ]:
            self.ui.jobSelector.addItem("Select...")
        self.teamList = self.api.team.get_list()
        self.ui.teamSelector.addItems([team.name for team in self.teamList])
        self.ui.teamSelector.currentText = "Select..."
        self.ui.jobSelector.currentText = "Select..."
        self.ui.teamSelector.blockSignals(False)
        self.ui.jobSelector.blockSignals(False)
        self.ui.teamSelector.setEnabled(True)
        self.ui.jobSelector.setEnabled(False)

    @log_method_call
    def _deactivateTeamSelection(self) -> None:
        self.ui.teamSelector.blockSignals(True)
        self.ui.jobSelector.blockSignals(True)
        self.ui.teamSelector.clear()
        self.ui.teamSelector.addItem("Select...")
        self.ui.teamSelector.currentText = "Select..."
        self.ui.teamSelector.setEnabled(False)
        self.ui.jobSelector.clear()
        self.ui.jobSelector.addItem("Select...")
        self.ui.jobSelector.currentText = "Select..."
        self.ui.jobSelector.setEnabled(False)
        self.ui.teamJobs.setEnabled(False)
        self.ui.teamJobs.setChecked(False)
        self.ui.activeJob.setChecked(False)
        self.ui.activeJob.setEnabled(False)
        self.ui.teamSelector.blockSignals(False)
        self.ui.jobSelector.blockSignals(False)

    # --------------------------------------- Technical Methods -------------------------------------- #

    @log_method_call_args
    def incrementProgressBar(self, value):
        self.ui.progressBar.setValue(self.ui.progressBar.value + value)

    @log_method_call_args
    def _dowloadProject(self, downloadVolumes=True) -> None:
        """
        Download project from server and create keyIdMap and projectMeta.
        If tempPath is not None, download to tempPath and don't create keyIdMap and projectMeta.
        """

        # Drop a header left by an earlier download so it cannot block this one.
        self.api.headers.pop("x-job-id", None)
        self.api.add_header("x-job-id", str(self.activeJob.id))
        try:
            VolumeProject.download(
                self.api,
                self.activeJob.project_id,
                self.savePath,
                [self.activeJob.dataset_id],
                download_volumes=downloadVolumes,
                progress_cb=self.incrementProgressBar,
            )
        finally:
            self.api.headers.pop("x-job-id", None)
        self.ui.progressBar.reset()
        self.keyIdMap = KeyIdMap.load_json(f"{self.savePath}/key_id_map.json")
        with open(f"{self.savePath}/meta.json", "r") as f:
            metaJson = load(f)
        self.projectMeta = ProjectMeta.from_json(metaJson)

    @log_method_call
    def _createAnnObject(self) -> None:

        with open(
            f"{self.savePath}/{self.activeJob.dataset_name}/ann/{self.volume.name}.json",
            "r",
        ) as f:
            annJson = load(f)
        self.ann = VolumeAnnotation.from_json(annJson, self.projectMeta)

    @log_method_call
    def _getUserName(self):
        user_info = self.api.user.get_my_info()
        if user_info.name:
            self.userName = user_info.name
        else:
            self.userName = user_info.login

    @log_method_call_args
    def _getItemFromSelector(self, itemsList, selectorText) -> None:
        """Get item by name from selector"""
        for item in itemsList:
            if item.name == selectorText:
                return item

    @log_method_call_args
    def _removeNodesFromScene(self, collection) -> None:
        if collection.GetNumberOfItems() == 0:
            return
        collection.InitTraversal()
        node = collection.GetNextItemAsObject()
        while node is not None:
            slicer.mrmlScene.RemoveNode(node)
            node = collection.GetNextItemAsObject()

    @log_method_call
    def _setVolumeIcon(self) -> None:
        """Set icon to volumeSelector items according to their reviewStatus."""
        moduleDir = os.path.dirname(slicer.util.modulePath(self.__module__))
        acceptedPath = os.path.join(moduleDir, "Resources/Icons", "accepted.svg")
        rejectedPath = os.path.join(moduleDir, "Resources/Icons", "rejected.svg")
        donePath = os.path.join(moduleDir, "Resources/Icons", "done.svg")
        nonePath = os.path.join(moduleDir, "Resources/Icons", "none.svg")
        self.ui.volumeSelector.blockSignals(True)
        for entity in self.activeJob.entities:
            if entity["reviewStatus"] == "accepted":
                icon = qt.QIcon(acceptedPath)
            elif entity["reviewStatus"] == "rejected":
                icon = qt.QIcon(rejectedPath)
            elif entity["reviewStatus"] == "done":
                icon = qt.QIcon(donePath)
            elif entity["reviewStatus"] == "none":
                icon = qt.QIcon(nonePath)
            idx = self.ui.volumeSelector.findText(entity["name"])
            if idx < 0:
                logging.warning(
                    f'Volume {entity["name"]} is not found in the list when setting "{icon}" icon'
                )
                continue
            self.ui.volumeSelector.setItemIcon(idx, icon)
        self.ui.volumeSelector.blockSignals(False)

    @log_method_call
    def _refreshJobInfo(self) -> None:
        self.activeJob = self.api.labeling_job.get_info_by_id(self.activeJob.id)

    @log_method_call_args
    def _createColorIcon(self, color):
        # TODO refactor to use tag.svg with custom color
        pixmap = qt.QPixmap(10, 10)
        pixmap.fill(qt.QColor(color[0], color[1], color[2]))
        icon = qt.QIcon(pixmap)
        return icon
